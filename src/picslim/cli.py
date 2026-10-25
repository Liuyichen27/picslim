#!/usr/bin/env python
"""picslim 命令行入口。

设计原则：
1. 只读原图，输出到另一个目录，绝不覆盖源文件。
2. 有目标体积时，先降质量，降到底还超标才缩尺寸。
3. 压不动就说清楚为什么，不假装成功。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - 只在依赖缺失时触发
    sys.exit("缺少 Pillow，请先执行：pip install pillow")

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

# 各格式真正支持的编码参数
_LOSSY = {"JPEG", "WEBP"}


def human_size(n: float) -> str:
    """把字节数变成人看的单位。"""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def parse_size(text: str) -> int:
    """解析 "500K" / "2MB" / "1.5m" 这类体积写法，返回字节数。"""
    t = text.strip().upper().replace(" ", "")
    units = {"B": 1, "K": 1024, "KB": 1024, "M": 1024 ** 2, "MB": 1024 ** 2,
             "G": 1024 ** 3, "GB": 1024 ** 3}
    for suffix in ("GB", "MB", "KB", "B", "G", "M", "K"):
        if t.endswith(suffix):
            num = t[: -len(suffix)]
            try:
                return int(float(num) * units[suffix])
            except ValueError:
                raise argparse.ArgumentTypeError(f"看不懂的体积：{text}")
    try:
        return int(float(t))
    except ValueError:
        raise argparse.ArgumentTypeError(f"看不懂的体积：{text}")


@dataclass
class Result:
    """单个文件的压缩结果。"""

    src: Path
    dst: Path | None
    src_size: int
    dst_size: int
    quality: int = 0
    scale: float = 1.0
    ok: bool = True
    note: str = ""

    @property
    def saved(self) -> int:
        return self.src_size - self.dst_size

    @property
    def ratio(self) -> float:
        if self.src_size <= 0:
            return 0.0
        return self.dst_size / self.src_size


def collect_images(targets: list[str], recursive: bool) -> list[Path]:
    """把用户给的路径摊平成一串图片文件。"""
    found: list[Path] = []
    for t in targets:
        p = Path(t).expanduser()
        if p.is_file():
            if p.suffix.lower() in SUPPORTED:
                found.append(p)
            else:
                print(f"  跳过（不是支持的图片）：{p}", file=sys.stderr)
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            found.extend(sorted(f for f in it if f.is_file() and f.suffix.lower() in SUPPORTED))
        else:
            print(f"  跳过（路径不存在）：{p}", file=sys.stderr)
    # 去重但保序
    seen: set[Path] = set()
    uniq = []
    for f in found:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def _target_format(img: Image.Image, src: Path, fmt: str) -> str:
    """决定输出格式：PIL 的 format 字符串。"""
    if fmt == "webp":
        return "WEBP"
    if fmt == "jpeg":
        return "JPEG"
    raw = (img.format or src.suffix.lstrip(".")).upper()
    return "JPEG" if raw in ("JPG", "JPEG") else raw


def _flatten_alpha(img: Image.Image, fmt: str) -> Image.Image:
    """JPEG 不支持透明通道，铺白底再存，否则 PIL 会直接抛错。"""
    if fmt == "JPEG" and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        return img.convert("RGB")
    return img


def _encode(img: Image.Image, fmt: str, quality: int) -> bytes:
    """把图片编码成字节，不落盘，方便反复试参数。"""
    import io

    buf = io.BytesIO()
    params: dict = {}
    if fmt in _LOSSY:
        params["quality"] = quality
        params["optimize"] = True
        if fmt == "WEBP":
            params["method"] = 6
    elif fmt == "PNG":
        # PNG 是无损的，quality 没用，只能拉满压缩等级
        params["optimize"] = True
        params["compress_level"] = 9
    img.save(buf, format=fmt, **params)
    return buf.getvalue()


def compress_one(
    src: Path,
    out_root: Path,
    base_root: Path,
    *,
    max_size: int | None = None,
    quality: int = 82,
    fmt: str = "keep",
    min_quality: int = 35,
    max_shrink: float = 0.5,
    dry_run: bool = False,
) -> Result:
    """压缩一个文件。返回结果对象，不抛异常（错误记在 note 里）。"""
    src_size = src.stat().st_size
    rel = src.relative_to(base_root) if base_root in src.parents or src.parent == base_root else Path(src.name)

    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)  # 手机照片的旋转信息要落实到位图
            target_fmt = _target_format(im, src, fmt)
            work = _flatten_alpha(im, target_fmt)

            ext = {"JPEG": ".jpg", "WEBP": ".webp", "PNG": ".png",
                   "GIF": ".gif", "BMP": ".bmp", "TIFF": ".tif"}.get(target_fmt, src.suffix.lower())
            dst = out_root / rel.with_suffix(ext)

            if dry_run:
                return Result(src, dst, src_size, src_size, note="dry-run")

            scale = 1.0
            best: tuple[bytes, int, float] | None = None
            q = max(quality, min_quality)

            # 外层循环负责缩尺寸，内层循环负责降质量
            while True:
                img_i = work if scale >= 1.0 else work.resize(
                    (max(1, int(work.width * scale)), max(1, int(work.height * scale))),
                    Image.LANCZOS,
                )
                cur_q = q
                while cur_q >= min_quality:
                    blob = _encode(img_i, target_fmt, cur_q)
                    if best is None or len(blob) < len(best[0]):
                        best = (blob, cur_q, scale)
                    if max_size is None or len(blob) <= max_size:
                        break
                    cur_q -= 8
                else:
                    # 质量降到底仍超标，缩一圈尺寸再来
                    if max_size is not None and scale > max_shrink:
                        scale *= 0.85
                        q = min(quality, cur_q + 8)
                        continue
                break

            assert best is not None
            blob, used_q, used_scale = best
            if target_fmt not in _LOSSY:
                used_q = 0  # 无损格式没有质量参数，别在报告里误导人
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(blob)

            note = ""
            if max_size is not None and len(blob) > max_size:
                note = "未能压到目标体积"
                if target_fmt == "PNG":
                    note += "（PNG 无损，试试 --format webp）"
            return Result(src, dst, src_size, len(blob), used_q, used_scale, True, note)

    except Exception as e:  # 单张失败不能拖垮整批
        return Result(src, None, src_size, 0, ok=False, note=f"{type(e).__name__}: {e}")


def _display_width(text: str) -> int:
    """终端里中文占两格，按显示宽度算，表格才不会歪。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度左对齐补空格。"""
    gap = width - _display_width(text)
    return text + " " * max(0, gap)


def _print_row(r: Result, out_root: Path, base_root: Path) -> None:
    raw = str(r.src.relative_to(base_root)) if base_root in r.src.parents else r.src.name
    name = raw.replace("\\", "/")
    if _display_width(name) > 46:
        while _display_width(name) > 45 and name:
            name = name[1:]
        name = "…" + name
    if not r.ok:
        print(f"  ✗ {_pad(name, 46)} {human_size(r.src_size):>9}  {r.note}")
        return
    sign = "↓" if r.saved >= 0 else "↑"
    pct = abs(r.saved) / r.src_size * 100 if r.src_size else 0
    extra = ""
    if r.scale < 0.999:
        extra += f" 缩放{int(r.scale * 100)}%"
    if r.quality:
        extra += f" q={r.quality}"
    if r.note:
        extra += f" {r.note}"
    print(
        f"  ✓ {_pad(name, 46)} {human_size(r.src_size):>9} → {human_size(r.dst_size):>9}"
        f"  {sign}{pct:4.0f}%{extra}"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="picslim",
        description="批量压缩图片体积。只读原图，结果写到另一个目录。",
        epilog="例子：picslim ./照片 -o ./out --max-size 500K --format webp",
    )
    p.add_argument("paths", nargs="+", help="图片文件或目录")
    p.add_argument("-o", "--out", default="picslim_out", help="输出目录（默认 picslim_out）")
    p.add_argument("-m", "--max-size", type=parse_size, default=None,
                   help="单张目标体积上限，如 500K / 2M")
    p.add_argument("-q", "--quality", type=int, default=82, help="起始质量 1-100（默认 82）")
    p.add_argument("--min-quality", type=int, default=35, help="质量下限（默认 35）")
    p.add_argument("-f", "--format", choices=["keep", "webp", "jpeg"], default="keep",
                   help="输出格式，webp 通常能再省一半体积")
    p.add_argument("-r", "--recursive", action="store_true", help="递归子目录")
    p.add_argument("-j", "--jobs", type=int, default=min(8, (os.cpu_count() or 4)),
                   help="并发数（默认 8）")
    p.add_argument("-n", "--dry-run", action="store_true", help="只列出会处理哪些文件，不写盘")
    p.add_argument("-V", "--version", action="version", version="picslim 1.0.0")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()

    targets = collect_images(args.paths, args.recursive)
    if not targets:
        print("没有找到可处理的图片。", file=sys.stderr)
        return 2

    out_root = Path(args.out).expanduser()
    # 以用户给的第一个目录为基准，保持相对目录结构
    first = Path(args.paths[0]).expanduser()
    base_root = first if first.is_dir() else first.parent

    print(f"共 {len(targets)} 张图片，输出到 {out_root}"
          + (f"，目标 {human_size(args.max_size)}" if args.max_size else ""))
    print()

    results: list[Result] = []
    if args.jobs <= 1 or len(targets) == 1:
        for f in targets:
            r = compress_one(f, out_root, base_root, max_size=args.max_size,
                             quality=args.quality, fmt=args.format,
                             min_quality=args.min_quality, dry_run=args.dry_run)
            results.append(r)
            _print_row(r, out_root, base_root)
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(compress_one, f, out_root, base_root,
                            max_size=args.max_size, quality=args.quality,
                            fmt=args.format, min_quality=args.min_quality,
                            dry_run=args.dry_run): f
                for f in targets
            }
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                _print_row(r, out_root, base_root)

    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    total_src = sum(r.src_size for r in ok)
    total_dst = sum(r.dst_size for r in ok)
    elapsed = time.time() - started

    print()
    if ok:
        saved = total_src - total_dst
        pct = saved / total_src * 100 if total_src else 0
        print(f"完成 {len(ok)} 张：{human_size(total_src)} → {human_size(total_dst)}"
              f"，省下 {human_size(saved)}（{pct:.0f}%），耗时 {elapsed:.1f}s")
        shrunk = sum(1 for r in ok if r.scale < 0.999)
        if shrunk:
            print(f"其中 {shrunk} 张为了达标做了缩放")
    if bad:
        print(f"失败 {len(bad)} 张，见上面的 ✗ 行", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
