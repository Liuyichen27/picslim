"""启动时顺手看一眼 GitHub 上有没有新版本。

写这个小东西的三条原则：

1. **不打扰** —— 只在交互式终端里提示，输出走 stderr，绝不污染正常结果；
2. **不拖慢** —— 结果缓存 24 小时，超时 2.5 秒就放弃，查不到当没这回事；
3. **不失礼** —— 没网、GitHub 抽风、接口改版，一律静默。

关掉它：设环境变量 ``PICSLIM_NO_UPDATE=1``。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

NAME = "picslim"
REPO = "Liuyichen27/picslim"
TIMEOUT = 2.5
OK_TTL = 24 * 3600       # 查到版本号后 24 小时内不再查
FAIL_TTL = 6 * 3600      # 查失败 6 小时后再试
PROXY = "https://gh-proxy.com/"


def _cache_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TMPDIR") or "/tmp"
    folder = Path(base) / ".cli-update-cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{NAME}.json"


def _read_cache() -> dict | None:
    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        ttl = OK_TTL if data.get("tag") else FAIL_TTL
        if time.time() - float(data.get("at") or 0) > ttl:
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


def _write_cache(tag: str) -> None:
    try:
        _cache_file().write_text(
            json.dumps({"tag": tag, "at": time.time()}), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _fetch_tag() -> str:
    """官方接口 → 镜像；先直连（无视系统代理），全挂了再走环境代理。"""
    last: Exception | None = None
    urls = (
        f"https://api.github.com/repos/{REPO}/releases/latest",
        f"{PROXY}https://api.github.com/repos/{REPO}/releases/latest",
    )
    for opener in (_DIRECT, None):
        for url in urls:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": NAME, "Accept": "application/vnd.github+json"},
                )
                resp = (opener or urllib.request).open(req, timeout=TIMEOUT)
                with resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                tag = str(data.get("tag_name") or "").strip()
                if tag:
                    return tag
            except Exception as e:  # noqa: BLE001
                last = e
    raise last or RuntimeError("没有可用的下载源")


def _tuple(ver: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in str(ver or "").lstrip("vV").split("."):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def check(version: str) -> None:
    """在 ``main()`` 最开头调一次。任何异常都在这里咽掉。"""
    try:
        if os.environ.get("PICSLIM_NO_UPDATE") == "1":
            return
        if not sys.stderr.isatty():          # 被管道/重定向时不插话
            return
        cached = _read_cache()
        if cached is None:
            tag = _fetch_tag()
            _write_cache(tag)
        else:
            tag = str(cached.get("tag") or "")
        if tag and _tuple(tag) > _tuple(version):
            print(
                f"\n发现新版本 {tag}（当前 v{version}）"
                f"\n  升级：pip install --upgrade git+https://github.com/{REPO}.git"
                f"\n  详情：https://github.com/{REPO}/releases/latest\n",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001
        pass
