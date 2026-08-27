"""PWA / APK 빌드용 PNG 아이콘 세트 생성.

  .venv\\Scripts\\python.exe scripts\\gen_icons.py

web/static/icons/ 에 다음을 만든다:
  icon-192.png, icon-512.png            (any)
  icon-maskable-192.png, -512.png       (maskable, 안전영역 여백 포함)
  apple-touch-icon.png (180)            (iOS 홈화면)
필요 패키지: pillow  (pip install pillow)
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

BG = (11, 18, 32)         # #0b1220
BLUE = (76, 141, 255)     # #4c8dff
GREEN = (47, 191, 113)    # #2fbf71

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "web", "static", "icons")


def draw(size: int, maskable: bool) -> Image.Image:
    # 4x 슈퍼샘플링 후 축소 = 안티에일리어싱
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 배경: maskable 은 꽉 찬 사각(원형 마스크 대비), 일반은 둥근 사각
    if maskable:
        d.rectangle([0, 0, s, s], fill=BG)
        content = 0.66            # 안전영역: 가운데 66%
    else:
        r = int(s * 0.22)
        d.rounded_rectangle([0, 0, s, s], radius=r, fill=BG)
        content = 0.82

    cx = cy = s / 2
    R = s * content / 2           # 콘텐츠 반경

    ring_w = max(2, int(R * 0.16))
    # 게이지 링
    d.ellipse([cx - R * 0.62, cy - R * 0.62, cx + R * 0.62, cy + R * 0.62],
              outline=BLUE, width=ring_w)
    # 중심 허브
    hub = R * 0.16
    d.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill=BLUE)

    # 상하좌우 파란 눈금
    tick_len = R * 0.30
    tick_w = max(2, int(R * 0.15))
    for ang in (0, 90, 180, 270):
        a = math.radians(ang)
        x0 = cx + math.cos(a) * R * 0.66
        y0 = cy + math.sin(a) * R * 0.66
        x1 = cx + math.cos(a) * (R * 0.66 + tick_len)
        y1 = cy + math.sin(a) * (R * 0.66 + tick_len)
        d.line([x0, y0, x1, y1], fill=BLUE, width=tick_w)

    # 대각 초록 눈금
    for ang in (45, 135, 225, 315):
        a = math.radians(ang)
        x0 = cx + math.cos(a) * R * 0.42
        y0 = cy + math.sin(a) * R * 0.42
        x1 = cx + math.cos(a) * R * 0.60
        y1 = cy + math.sin(a) * R * 0.60
        d.line([x0, y0, x1, y1], fill=GREEN, width=max(2, int(R * 0.12)))

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-192.png", 192, True),
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon.png", 180, True),
    ]
    for name, size, maskable in jobs:
        p = os.path.join(OUT, name)
        draw(size, maskable).save(p)
        print("wrote", os.path.relpath(p, os.path.dirname(OUT)))


if __name__ == "__main__":
    main()
