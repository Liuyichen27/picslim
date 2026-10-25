"""picslim 的最小可用测试集：不依赖网络，图片现场生成。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from picslim.cli import (  # noqa: E402
    collect_images,
    compress_one,
    human_size,
    parse_size,
)


def make_noisy_jpeg(path: Path, size=(1200, 900)) -> None:
    """造一张噪点图，这种图压不动，能真实反映压缩率。"""
    img = Image.new("RGB", size)
    px = img.load()
    seed = 12345
    for y in range(size[1]):
        for x in range(size[0]):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            px[x, y] = (seed & 0xFF, (seed >> 8) & 0xFF, (seed >> 16) & 0xFF)
    img.save(path, quality=98)


class TestParseSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_size("500K"), 500 * 1024)
        self.assertEqual(parse_size("2M"), 2 * 1024 ** 2)
        self.assertEqual(parse_size("1.5MB"), int(1.5 * 1024 ** 2))
        self.assertEqual(parse_size("1024"), 1024)
        self.assertEqual(parse_size("800kb"), 800 * 1024)

    def test_bad_input(self):
        with self.assertRaises(Exception):
            parse_size("abc")


class TestHumanSize(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(2048), "2.0 KB")
        self.assertEqual(human_size(5 * 1024 ** 2), "5.0 MB")


class TestCollect(unittest.TestCase):
    def test_skips_non_images_and_dedupes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.jpg").write_bytes(b"x")
            (root / "b.txt").write_bytes(b"x")
            (root / "sub").mkdir()
            (root / "sub" / "c.png").write_bytes(b"x")

            flat = collect_images([str(root)], recursive=False)
            self.assertEqual([p.name for p in flat], ["a.jpg"])

            deep = collect_images([str(root)], recursive=True)
            self.assertEqual(sorted(p.name for p in deep), ["a.jpg", "c.png"])

            # 同一个文件给两次，只处理一次
            twice = collect_images([str(root / "a.jpg"), str(root / "a.jpg")], recursive=False)
            self.assertEqual(len(twice), 1)


class TestCompress(unittest.TestCase):
    def test_hits_target_size(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "noisy.jpg"
            make_noisy_jpeg(src)
            before = src.stat().st_size
            limit = 150 * 1024

            r = compress_one(src, root / "out", root, max_size=limit)

            self.assertTrue(r.ok, r.note)
            self.assertLessEqual(r.dst_size, limit, "没压到目标体积")
            self.assertLess(r.dst_size, before, "压完反而变大了")
            self.assertTrue(r.dst.exists())
            # 原图一个字节都不能动
            self.assertEqual(src.stat().st_size, before)

    def test_keeps_folder_structure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "2024").mkdir()
            src = root / "2024" / "a.jpg"
            make_noisy_jpeg(src, size=(400, 300))

            r = compress_one(src, root / "out", root)
            self.assertTrue(r.ok, r.note)
            self.assertEqual(r.dst.relative_to(root / "out"), Path("2024/a.jpg"))

    def test_alpha_png_to_jpeg_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "alpha.png"
            img = Image.new("RGBA", (200, 200), (255, 0, 0, 0))
            ImageDraw.Draw(img).ellipse((20, 20, 180, 180), fill=(0, 128, 255, 255))
            img.save(src)

            r = compress_one(src, root / "out", root, fmt="jpeg")
            self.assertTrue(r.ok, r.note)
            self.assertEqual(r.dst.suffix, ".jpg")
            with Image.open(r.dst) as check:
                self.assertEqual(check.mode, "RGB")

    def test_webp_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "a.jpg"
            make_noisy_jpeg(src, size=(500, 400))

            r = compress_one(src, root / "out", root, fmt="webp", quality=75)
            self.assertTrue(r.ok, r.note)
            self.assertEqual(r.dst.suffix, ".webp")
            with Image.open(r.dst) as check:
                self.assertEqual(check.format, "WEBP")

    def test_corrupt_file_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "broken.jpg"
            src.write_bytes(b"not an image")

            r = compress_one(src, root / "out", root)
            self.assertFalse(r.ok)
            self.assertIn("Error", r.note)


if __name__ == "__main__":
    unittest.main()
