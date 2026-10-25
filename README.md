# PicSlim · 图片批量压缩

[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-12B76A)](LICENSE)

把一堆图片压小，尽量看不出区别。只读原图，结果写到另一个目录，**永远不会覆盖你的源文件**。

```bash
picslim ./照片 -o ./压缩后 --max-size 500K
```

## 它解决什么问题

手机拍的照片动辄 4 MB、公众号配图上传限制 2 MB、网页加载一屏图要十几秒——这些都需要压缩，但：

- 在线压缩站要上传你的图，隐私和速度都不合适
- 图形软件一张张导入导出，几十张就崩溃
- 命令行工具要么只支持单文件，要么参数难记

PicSlim 就是一条命令处理一整个目录：给个体积上限，它自己去找合适的质量和尺寸。

## 安装

```bash
pip install git+https://github.com/Liuyichen27/picslim.git
```

或者本地跑：

```bash
git clone https://github.com/Liuyichen27/picslim.git
cd picslim
pip install -e .
picslim --help
```

## 常用例子

```bash
# 整个目录压到每张不超过 500 KB
picslim ./照片 --max-size 500K

# 转成 webp，通常比 jpeg 再小一半
picslim ./照片 -f webp -q 80

# 只看看会处理哪些文件，不写盘
picslim ./照片 -n

# 递归子目录，输出到指定位置
picslim ./素材 -r -o ./压缩后 -j 4
```

## 参数

| 参数 | 说明 |
| --- | --- |
| `-o, --out` | 输出目录，默认 `picslim_out` |
| `-m, --max-size` | 单张体积上限，支持 `500K` / `2M` / `1.5M` |
| `-q, --quality` | 起始质量，1-100，默认 82 |
| `--min-quality` | 质量下限，默认 35，防止压成马赛克 |
| `-f, --format` | `keep`（默认）/ `webp` / `jpeg` |
| `-r, --recursive` | 递归子目录 |
| `-j, --jobs` | 并发数，默认 8 |
| `-n, --dry-run` | 只列文件不写盘 |

## 压缩策略

有 `--max-size` 时，按这个顺序试：

1. 用起始质量编码，达标就停
2. 不达标就每次降 8 点质量，一直降到 `--min-quality`
3. 还是超标，就把尺寸缩到 85%，重新走一遍上面的流程
4. 尺寸缩到原图 50% 仍超标，就接受最接近的结果，并在报告里标出来

没有 `--max-size` 时只降一次质量，然后就把图写出去——够快。

**关于 PNG**：PNG 是无损格式，`quality` 对它无效，只能拉满压缩等级。如果目标体积压不下来，工具会提示你加 `-f webp`，不会假装成功。

## 输出长什么样

```
共 37 张图片，输出到 压缩后，目标 500.0 KB

  ✓ IMG_0231.jpg                                   4.1 MB →  412.7 KB  ↓ 90%  q=66
  ✓ IMG_0232.jpg                                   3.8 MB →  468.2 KB  ↓ 88%  q=74
  ✓ 截图/微信图片_20240118.png                      1.2 MB →  298.0 KB  ↓ 75%  q=0
  ✗ 素材/扫描件.png                                  8.4 MB →           未能压到目标体积（PNG 无损，试试 --format webp）

完成 36 张：128.4 MB → 14.2 MB，省下 114.2 MB（89%），耗时 6.4s
其中 3 张为了达标做了缩放
```

## 几个细节

- **会自动纠正手机照片的方向**：读 EXIF 的 Orientation 并真正旋转像素，压完不会横过来
- **JPEG 不支持透明**：遇到带透明通道的图会铺白底再编码，不会报错也不会变黑
- **单张失败不影响整批**：某个文件损坏只会在报告里标 ✗，其余照常处理
- **保持目录结构**：`素材/2024/a.jpg` 压完还是 `输出/2024/a.jpg`

## 作为库调用

```python
from pathlib import Path
from picslim.cli import compress_one

r = compress_one(Path("a.jpg"), Path("out"), Path("."), max_size=500 * 1024)
print(r.src_size, "->", r.dst_size, "质量", r.quality)
```

## 测试

```bash
python -m unittest discover -s tests -v
```

## 许可

MIT License，详见 [LICENSE](LICENSE)。

---

<sub>English: PicSlim is a CLI batch image compressor. It never overwrites your originals: results go to a separate output folder. Give it `--max-size 500K` and it will search quality first, then dimensions, until each image fits. Works with JPEG / PNG / WebP / BMP / TIFF, keeps folder structure, corrects EXIF rotation, and handles alpha channels when encoding to JPEG.</sub>
