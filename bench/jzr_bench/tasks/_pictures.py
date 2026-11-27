"""Photo-like test pictures: smooth gradients and shapes with a little sensor-like noise."""

from __future__ import annotations

import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

PALETTES = (
    ((200, 60, 40), (40, 70, 190), (240, 200, 80)),
    ((30, 150, 70), (230, 220, 60), (90, 40, 120)),
    ((150, 50, 170), (40, 200, 210), (250, 140, 30)),
    ((90, 90, 100), (220, 180, 150), (30, 110, 160)),
)


def photo_like(size: tuple[int, int], seed: int, *, noise: float = 6.0) -> Image.Image:
    """A deterministic RGB picture that compresses like a photo, different for each seed."""
    rng = random.Random(seed)
    small = (size[0] // 8, size[1] // 8)
    a, b, c = PALETTES[seed % len(PALETTES)]

    # a diagonal two-colour gradient
    mask = Image.linear_gradient("L").rotate(rng.uniform(20, 70), expand=False).resize(small)
    base = Image.composite(Image.new("RGB", small, a), Image.new("RGB", small, b), mask)

    # soft shapes at seed-dependent places
    draw = ImageDraw.Draw(base)
    for _ in range(14):
        x, y = rng.uniform(0, small[0]), rng.uniform(0, small[1])
        r = rng.uniform(small[0] * 0.03, small[0] * 0.16)
        colour = tuple(min(255, max(0, v + rng.randint(-60, 60))) for v in rng.choice((a, b, c)))
        if rng.random() < 0.5:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)
        else:
            draw.rectangle((x - r, y - r * 0.6, x + r, y + r * 0.6), fill=colour)
    base = base.filter(ImageFilter.GaussianBlur(small[0] / 300))
    picture = base.resize(size, Image.Resampling.BICUBIC)

    if noise > 0:
        grain = Image.effect_noise(size, noise).convert("RGB")  # centred on 128
        picture = ImageChops.add(picture, grain, scale=1.0, offset=-128)
    return picture
