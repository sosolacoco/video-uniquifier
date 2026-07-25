#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_dots.py
================

Генерирует набор ЕЛЕ ЗАМЕТНЫХ PNG-«фейк-точек» (тап-индикаторов) в папку overlays/.

Точки полупрозрачные, с мягкими краями (без резких границ), разных стилей и размеров.
Скрипт video_uniquifier потом сам ставит СЛУЧАЙНОЕ число точек в СЛУЧАЙНЫЕ места
на каждое видео + дополнительно приглушает их прозрачность (настройка в GUI).

Запуск:
    python generate_dots.py            # 20 точек в ./overlays
    python generate_dots.py --count 30 --out overlays --seed 1
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# Стили и их доля в наборе.
STYLES = ["dot", "dot", "glow", "ring", "ring", "ripple"]
SIZES = [40, 48, 56, 64, 72, 84, 96, 112, 128, 144]
# Мягкие светлые оттенки (тап-индикаторы обычно светлые/белые).
COLORS = [(255, 255, 255), (248, 248, 252), (255, 250, 242), (240, 244, 255)]


def make_mask(size: int, style: str, rng: random.Random, ss: int = 4) -> Image.Image:
    """Серый альфа-макс (0..255) формы точки, супер-сэмплинг + блюр для гладких краёв."""
    S = size * ss
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    c = S / 2
    pad = S * 0.14
    R = S / 2 - pad

    if style == "dot":
        r = R * rng.uniform(0.45, 0.70)
        d.ellipse([c - r, c - r, c + r, c + r], fill=255)
        blur = r * rng.uniform(0.55, 0.95)
    elif style == "glow":
        r = R * rng.uniform(0.22, 0.38)
        d.ellipse([c - r, c - r, c + r, c + r], fill=255)
        blur = r * rng.uniform(1.4, 2.2)
    elif style == "ring":
        r = R * rng.uniform(0.60, 0.85)
        w = max(2, int(R * rng.uniform(0.06, 0.12)))
        d.ellipse([c - r, c - r, c + r, c + r], outline=255, width=w)
        blur = w * rng.uniform(0.9, 1.6)
    else:  # ripple — два концентрических кольца + слабый центр
        r1 = R * rng.uniform(0.32, 0.48)
        r2 = R * rng.uniform(0.72, 0.90)
        w = max(2, int(R * rng.uniform(0.04, 0.08)))
        d.ellipse([c - r2, c - r2, c + r2, c + r2], outline=200, width=w)
        d.ellipse([c - r1, c - r1, c + r1, c + r1], outline=255, width=w)
        rc = R * 0.12
        d.ellipse([c - rc, c - rc, c + rc, c + rc], fill=170)
        blur = w * rng.uniform(1.1, 1.8)

    m = m.filter(ImageFilter.GaussianBlur(blur))
    return m.resize((size, size), Image.LANCZOS)


def make_dot(size: int, style: str, rng: random.Random) -> Image.Image:
    mask = make_mask(size, style, rng)
    max_alpha = rng.randint(26, 68)          # низкая непрозрачность = «незаметно»
    mask = mask.point(lambda a: a * max_alpha // 255)
    color = rng.choice(COLORS)
    img = Image.new("RGBA", (size, size), color + (0,))
    img.putalpha(mask)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description="Генератор PNG фейк-точек для overlays/")
    ap.add_argument("--count", type=int, default=20, help="Сколько точек создать")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "overlays")
    ap.add_argument("--seed", type=int, default=None, help="Seed (для повторяемости)")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)

    made = 0
    for i in range(a.count):
        style = rng.choice(STYLES)
        size = rng.choice(SIZES)
        img = make_dot(size, style, rng)
        path = a.out / f"dot_{i + 1:02d}_{style}_{size}.png"
        img.save(path)
        made += 1
        print(f"  + {path.name}  (alpha<=~68, {size}px)")

    print(f"\nГотово: {made} точек в {a.out}")
    print("Теперь в GUI включи «Накладывать фейк-точки» — они будут ставиться случайно.")


if __name__ == "__main__":
    main()
