#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鹦鹉全球金融早报 - LOGO PNG 生成脚本
用纯 Python 内置库（zlib + struct）生成 512x512 PNG，不依赖 Pillow/PIL。
- 背景：深蓝 #1a3a5c（品牌色）
- 中心：暖橙 #e8850e 圆形（辅助色，象征鹦鹉羽色）
- 中心叠加：白色"🦜"占位文字区域
生成后可被用户用真实 logo 文件覆盖。
"""
import zlib
import struct
import os

WIDTH, HEIGHT = 512, 512

# 品牌色
BG_R, BG_G, BG_B = 0x1a, 0x3a, 0x5c       # 深蓝 #1a3a5c
ACCENT_R, ACCENT_G, ACCENT_B = 0xe8, 0x85, 0x0e  # 暖橙 #e8850e
WHITE_R, WHITE_G, WHITE_B = 0xff, 0xff, 0xff

def make_chunk(chunk_type: str, data: bytes) -> bytes:
    """构造 PNG chunk: length(4) + type(4) + data + CRC32(4)"""
    c = chunk_type.encode('ascii')
    crc = zlib.crc32(c + data) & 0xffffffff
    return struct.pack('>I', len(data)) + c + data + struct.pack('>I', crc)

def generate_png(output_path: str):
    # PNG signature
    sig = b'\x89PNG\r\n\x1a\n'

    # IHDR
    ihdr_data = struct.pack('>IIBBBBB', WIDTH, HEIGHT, 8, 2, 0, 0, 0)  # 8bit, RGB, deflate, filter adaptive, no interlace
    ihdr = make_chunk('IHDR', ihdr_data)

    # 构造原始像素（每行前置 filter byte = 0）
    cx, cy = WIDTH // 2, HEIGHT // 2          # 中心
    outer_r = 150                              # 大圆半径
    inner_r = 70                               # 内圆（白）
    # 鹦鹉嘴/眼用小圆点缀
    eye_x, eye_y = cx - 40, cy - 30
    eye_r = 18

    raw = bytearray()
    for y in range(HEIGHT):
        raw.append(0)  # filter: None
        for x in range(WIDTH):
            dx, dy = x - cx, y - cy
            dist = (dx*dx + dy*dy) ** 0.5
            # 外环：暖橙
            if inner_r < dist <= outer_r:
                raw.extend([ACCENT_R, ACCENT_G, ACCENT_B])
            # 中心圆：白色
            elif dist <= inner_r:
                # 鹦鹉眼：深蓝小点
                edx, edy = x - eye_x, y - eye_y
                if (edx*edx + edy*edy) ** 0.5 <= eye_r:
                    raw.extend([BG_R, BG_G, BG_B])
                else:
                    raw.extend([WHITE_R, WHITE_G, WHITE_B])
            # 背景：深蓝
            else:
                raw.extend([BG_R, BG_G, BG_B])

    # IDAT: zlib 压缩
    compressed = zlib.compress(bytes(raw), 9)
    idat = make_chunk('IDAT', compressed)

    # IEND
    iend = make_chunk('IEND', b'')

    out = sig + ihdr + idat + iend
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(out)
    print(f"✅ PNG 已生成: {output_path}  ({len(out)} bytes)")

if __name__ == '__main__':
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'yinwu.png')
    generate_png(out)
