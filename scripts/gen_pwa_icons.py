#!/usr/bin/env python3
"""生成 PWA 图标（P1-78）。

**为什么用脚本而不是设计稿**：应用图标在本批只需要满足"可安装"这一条硬需求
（manifest 需要 192 / 512 两张，加一张 maskable 安全区版本）。当前仓库没有
设计侧产物，而"缺一个图标"不该阻塞整个 PWA 基建——先用**确定性脚本**画出
一个可用的占位标记：品牌色圆角方底 + 三条白色横杠（"头条"= 列表的隐喻，
三条长度递变，远看也认得出）。

后续要换设计稿时，直接覆盖 `public/icons/*.png` 即可，**没有任何代码引用这里的路径**
（manifest 引的也是产物文件名）。

输出为 8-bit RGBA PNG，无第三方依赖（zlib + struct 手写）。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

BRAND = (0x2D, 0x6C, 0xFF, 0xFF)  # #2d6cff，tokens.css 的 --color-primary（浅色）
WHITE = (0xFF, 0xFF, 0xFF, 0xFF)
OUT = Path(__file__).resolve().parents[1] / "mingri-toutiao-vue" / "public" / "icons"

SIZES = {"icon-192.png": 192, "icon-512.png": 512, "icon-maskable-512.png": 512}


def _chunk(tag: str, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _write_png(path: Path, px: list[list[tuple[int, int, int, int]]], size: int) -> None:
    raw = b"".join(
        b"\x00" + b"".join(struct.pack(">4B", *p) for p in row) for row in px
    )
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _rounded_rect_mask(size: int, radius: float) -> list[list[bool]]:
    r2 = radius * radius
    return [
        [
            (x + 0.5 - radius) ** 2 + (y + 0.5 - radius) ** 2 <= r2
            or (x >= radius and x < size - radius)
            or (y >= radius and y < size - radius)
            or (x + 0.5 - (size - radius)) ** 2 + (y + 0.5 - (size - radius)) ** 2 <= r2
            or (x + 0.5 - radius) ** 2 + (y + 0.5 - (size - radius)) ** 2 <= r2
            or (x + 0.5 - (size - radius)) ** 2 + (y + 0.5 - radius) ** 2 <= r2
            for x in range(size)
        ]
        for y in range(size)
    ]


def _render(size: int, maskable: bool) -> list[list[tuple[int, int, int, int]]]:
    """品牌圆角方底 + 三条白杠（长度递变）。

    maskable 版把图案压进中心 80% 安全区（Android 会自行裁成圆形/方形），
    底图仍是全幅——否则安全区外露白，裁切后边缘会出现难看的白边。
    """
    bg = _rounded_rect_mask(size, size * 0.22)
    # 三条杠的中心与尺寸（相对画布）
    safe = 0.80 if maskable else 1.0
    bar_w = size * 0.46 * safe
    bar_h = size * 0.075
    gap = size * 0.055
    total = bar_h * 3 + gap * 2
    top = (size - total) / 2
    widths = [1.0, 0.66, 0.86]  # 长度递变：像一列标题

    px: list[list[tuple[int, int, int, int]]] = []
    for y in range(size):
        row: list[tuple[int, int, int, int]] = []
        for x in range(size):
            if not bg[y][x]:
                row.append((0, 0, 0, 0))
                continue
            on_bar = False
            for i, w in enumerate(widths):
                cy = top + i * (bar_h + gap)
                half = bar_w * w / 2
                if top + i * (bar_h + gap) <= y < cy + bar_h and abs(
                    x + 0.5 - size / 2
                ) <= half:
                    on_bar = True
                    break
            row.append(WHITE if on_bar else BRAND)
        px.append(row)
    return px


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        maskable = name.startswith("icon-maskable")
        _write_png(OUT / name, _render(size, maskable), size)
        print(f"wrote {name} ({size}x{size}{', maskable' if maskable else ''})")


if __name__ == "__main__":
    main()
