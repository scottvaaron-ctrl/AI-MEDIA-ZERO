"""PillowCardProvider: renders 1080x1920 scene cards, stat/quote cards, and thumbnails. Cost: $0.

Each scene card = background (deterministic gradient per video) + optional licensed photo
(cover-fit, darkened) + headline caption + small attribution + AI disclosure line.
Narration subtitles are *not* baked in here; the renderer burns them from an ASS file so
they are timed to the audio.
"""

from __future__ import annotations

import colorsys
import hashlib
import logging
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from aimz.providers.base import HealthStatus, ImageProvider, ProviderContext

log = logging.getLogger("aimz.image.cards")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        path,
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        try:
            if candidate and Path(candidate).exists():
                return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _palette(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    r1, g1, b1 = colorsys.hsv_to_rgb(h, 0.55, 0.28)
    r2, g2, b2 = colorsys.hsv_to_rgb((h + 0.08) % 1, 0.65, 0.12)
    ra, ga, ba = colorsys.hsv_to_rgb((h + 0.5) % 1, 0.75, 0.95)
    return (
        (int(r1 * 255), int(g1 * 255), int(b1 * 255)),
        (int(r2 * 255), int(g2 * 255), int(b2 * 255)),
        (int(ra * 255), int(ga * 255), int(ba * 255)),
    )


def _gradient(w: int, h: int, c1: tuple[int, int, int], c2: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGB", (1, h))
    px = base.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))  # type: ignore[index]
    return base.resize((w, h))


def _cover_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    src_ratio = img.width / img.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_h = h
        new_w = int(h * src_ratio)
    else:
        new_w = w
        new_h = int(w / src_ratio)
    img = img.resize((max(1, new_w), max(1, new_h)), Image.Resampling.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for word in words:
        trial = " ".join([*cur, word])
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur.append(word)
        else:
            lines.append(" ".join(cur))
            cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _draw_centered_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: Any,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int],
    stroke: int = 0,
    line_gap: float = 1.15,
    max_lines: int = 6,
) -> None:
    x0, y0, x1, y1 = box
    lines = _wrap(draw, text, font, x1 - x0)[:max_lines]
    if not lines:
        return
    bbox = font.getbbox("Ag")
    lh = int((bbox[3] - bbox[1]) * line_gap)
    total = lh * len(lines)
    y = y0 + max(0, ((y1 - y0) - total) // 2)
    for line in lines:
        tw = draw.textlength(line, font=font)
        x = x0 + ((x1 - x0) - tw) / 2
        draw.text((x, y), line, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += lh


class PillowCardProvider(ImageProvider):
    name = "PillowCardProvider"
    is_paid = False

    def __init__(self, font_file: str = "", width: int = 1080, height: int = 1920):
        self.font_file = font_file
        self.width = width
        self.height = height

    def health(self) -> HealthStatus:
        try:
            _font(self.font_file, 20)
            return HealthStatus(
                True,
                f"Pillow OK; font {'found' if self.font_file and Path(self.font_file).exists() else 'fallback'}",
            )
        except Exception as exc:
            return HealthStatus(False, f"Pillow font error: {exc}")

    def render_card(self, ctx: ProviderContext, spec: dict[str, Any], out_path: Path) -> Path:
        """spec keys: kind, seed, headline, body, image_path, attribution, disclosure, label.

        The layout reserves the bottom ~30% of the frame for burned subtitles, so card text
        never collides with narration captions.
        """
        with self.authorized(ctx, "render_card"):
            w, h = self.width, self.height
            c1, c2, accent = _palette(str(spec.get("seed", "aimz")))
            canvas = _gradient(w, h, c1, c2)
            draw = ImageDraw.Draw(canvas)
            kind = spec.get("kind", "text_card")
            headline = (spec.get("headline") or "").strip()
            body = (spec.get("body") or "").strip()
            image_path = spec.get("image_path")
            subtitle_top = int(h * 0.68)  # nothing below this except attribution/disclosure lines

            head_font = _font(self.font_file, 84 if len(headline) < 40 else 68)
            body_font = _font(self.font_file, 54)
            small_font = _font(self.font_file, 30)
            label_font = _font(self.font_file, 36)

            has_image = False
            text_box = (80, 360, w - 80, subtitle_top - 60)
            if image_path and Path(image_path).exists():
                try:
                    ph = int(h * 0.60)
                    photo = _cover_fit(Image.open(image_path).convert("RGB"), w, ph)
                    overlay = Image.new("RGBA", (w, ph), (0, 0, 0, 0))
                    od = ImageDraw.Draw(overlay)
                    fade = 420
                    for y in range(ph - fade, ph):
                        a = int(255 * ((y - (ph - fade)) / fade) * 0.92)
                        od.line([(0, y), (w, y)], fill=(c2[0], c2[1], c2[2], a))
                    photo = Image.alpha_composite(photo.convert("RGBA"), overlay).convert("RGB")
                    canvas.paste(photo, (0, 0))
                    draw = ImageDraw.Draw(canvas)
                    has_image = True
                    text_box = (80, ph - 400, w - 80, subtitle_top - 40)
                except Exception as exc:  # corrupt download etc.
                    log.warning("could not use image %s: %s", image_path, exc)
            if not has_image:
                draw.rounded_rectangle((80, 300, 80 + 220, 300 + 16), radius=8, fill=accent)

            if kind in {"stat_card", "quote_card"}:
                if kind == "stat_card":
                    size = 110 if has_image else 150
                else:
                    size = 64 if has_image else 76
                    headline = "\u201c" + headline + "\u201d"
                big_font = _font(self.font_file, size)
                if body:
                    split = text_box[1] + int((text_box[3] - text_box[1]) * 0.62)
                    _draw_centered_block(
                        draw,
                        headline,
                        big_font,
                        (text_box[0], text_box[1], text_box[2], split),
                        (255, 255, 255),
                        stroke=2,
                        max_lines=3,
                    )
                    _draw_centered_block(
                        draw,
                        body,
                        body_font,
                        (text_box[0], split, text_box[2], text_box[3]),
                        accent,
                        max_lines=2,
                    )
                else:
                    _draw_centered_block(
                        draw, headline, big_font, text_box, (255, 255, 255), stroke=2, max_lines=4
                    )
            else:
                _draw_centered_block(
                    draw, headline, head_font, text_box, (255, 255, 255), stroke=3, max_lines=5
                )

            label = spec.get("label")
            if label:
                lw = draw.textlength(label, font=label_font) + 48
                draw.rounded_rectangle((80, 200, 80 + lw, 200 + 64), radius=32, fill=accent)
                draw.text((104, 210), label, font=label_font, fill=(20, 20, 20))

            y = h - 210
            attribution = (spec.get("attribution") or "").strip()
            if attribution:
                for line in textwrap.wrap(attribution, width=56)[:2]:
                    draw.text((80, y), line, font=small_font, fill=(210, 210, 210))
                    y += 36
            disclosure = (spec.get("disclosure") or "").strip()
            if disclosure:
                for line in textwrap.wrap(disclosure, width=56)[:2]:
                    draw.text((80, y), line, font=small_font, fill=(180, 180, 180))
                    y += 36

            out_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(out_path, "PNG", optimize=False)
            return out_path

    def render_thumbnail(
        self, ctx: ProviderContext, title: str, seed: str, out_path: Path, image_path: str | None = None
    ) -> Path:
        spec = {
            "kind": "text_card",
            "seed": seed,
            "headline": title,
            "image_path": image_path,
            "label": "EXPLAINER",
        }
        p = self.render_card(ctx, spec, out_path)
        try:
            img = Image.open(p).filter(ImageFilter.UnsharpMask(radius=2, percent=80))
            img.save(p, "PNG")
        except Exception:
            pass
        return p
