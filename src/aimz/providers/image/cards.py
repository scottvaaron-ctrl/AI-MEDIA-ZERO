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
from aimz.providers.video import layout

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


def _palette(
    seed: str, hue: float | None = None
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """Background, gradient end and accent colours: from ``hue`` (0-1) when given, else from a hash of ``seed``."""
    h = (hue % 1.0) if hue is not None else int(hashlib.sha256(seed.encode()).hexdigest()[:6], 16) / 0xFFFFFF
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


def _fit(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_file: str,
    size: int,
    box: tuple[int, int, int, int],
    max_lines: int,
    line_gap: float = 1.15,
) -> Any:
    """The largest font from ``size`` down to 60% of it whose wrapped text fits the box in ``max_lines``.

    Text used to be cut after ``max_lines`` lines, which dropped the end of a long headline.
    """
    x0, y0, x1, y1 = box
    font = _font(font_file, size)
    for s in range(size, max(24, int(size * 0.6)) - 1, -4):
        font = _font(font_file, s)
        lines = _wrap(draw, text, font, x1 - x0)
        bbox = font.getbbox("Ag")
        if len(lines) <= max_lines and len(lines) * (bbox[3] - bbox[1]) * line_gap <= (y1 - y0):
            return font
    return font


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
        """spec keys: kind, seed, headline, body, image_path, attribution, disclosure, label, and the
        per-video settings palette_hue (0-1 or None), show_headline (default True), counter ("3/7" or None),
        template ("card", "full_bleed", "ranking"), rank (the number a ranking card shows) and overlay
        (True: a transparent layer of text for a video-clip scene).

        Everything stays inside the platform's safe area (``providers/video/layout.py``): card text sits
        in the upper half, credits just below it, and the band above the platform's bottom overlay is kept
        free for the burned narration captions.
        """
        template = str(spec.get("template") or "card")
        image_ok = bool(spec.get("image_path")) and Path(str(spec.get("image_path"))).exists()
        if spec.get("overlay"):
            with self.authorized(ctx, "render_card"):
                return self._render_layered(spec, out_path, None)
        if template == "full_bleed" and image_ok:
            with self.authorized(ctx, "render_card"):
                return self._render_layered(spec, out_path, Path(str(spec["image_path"])))
        with self.authorized(ctx, "render_card"):
            w, h = self.width, self.height
            hue = spec.get("palette_hue")
            c1, c2, accent = _palette(str(spec.get("seed", "aimz")), float(hue) if hue is not None else None)
            canvas = _gradient(w, h, c1, c2)
            draw = ImageDraw.Draw(canvas)
            kind = spec.get("kind", "text_card")
            headline = (spec.get("headline") or "").strip()
            body = (spec.get("body") or "").strip()
            image_path = spec.get("image_path")
            text_bottom = int(h * layout.CARD_TEXT_BOTTOM)  # captions and credits live below this

            head_font = _font(self.font_file, 84 if len(headline) < 40 else 68)
            body_font = _font(self.font_file, 54)
            small_font = _font(self.font_file, 30)
            label_font = _font(self.font_file, 36)

            has_image = False
            text_box = (80, int(h * layout.CARD_TEXT_TOP), w - 80, text_bottom)
            if image_path and Path(image_path).exists():
                try:
                    ph = int(h * layout.CARD_TEXT_BOTTOM)
                    photo = _cover_fit(Image.open(image_path).convert("RGB"), w, ph)
                    overlay = Image.new("RGBA", (w, ph), (0, 0, 0, 0))
                    od = ImageDraw.Draw(overlay)
                    fade = 360
                    for y in range(ph - fade, ph):
                        a = int(255 * ((y - (ph - fade)) / fade) * 0.92)
                        od.line([(0, y), (w, y)], fill=(c2[0], c2[1], c2[2], a))
                    photo = Image.alpha_composite(photo.convert("RGBA"), overlay).convert("RGB")
                    canvas.paste(photo, (0, 0))
                    draw = ImageDraw.Draw(canvas)
                    has_image = True
                    text_box = (80, ph - 400, w - 80, text_bottom)
                except Exception as exc:  # corrupt download etc.
                    log.warning("could not use image %s: %s", image_path, exc)
            if not has_image:
                bar_y = int(h * layout.CARD_TEXT_TOP) - 36
                draw.rounded_rectangle((80, bar_y, 80 + 220, bar_y + 16), radius=8, fill=accent)
            if template == "ranking" and spec.get("rank") is not None:
                # A big number above the headline, for list-style videos.
                num_font = _font(self.font_file, 200)
                num = f"#{spec['rank']}"
                top = text_box[1]
                nw = draw.textlength(num, font=num_font)
                draw.text(
                    ((w - nw) / 2, top - 40),
                    num,
                    font=num_font,
                    fill=accent,
                    stroke_width=4,
                    stroke_fill=(0, 0, 0),
                )
                text_box = (text_box[0], top + 200, text_box[2], text_box[3])
            # The headline can be left off a photo; a card without a photo always keeps it, or it would be blank.
            show_headline = bool(spec.get("show_headline", True)) or not has_image

            if kind in {"stat_card", "quote_card"}:
                if kind == "stat_card":
                    size = 110 if has_image else 150
                else:
                    size = 64 if has_image else 76
                    headline = "\u201c" + headline + "\u201d"
                big_font = _fit(draw, headline, self.font_file, size, text_box, 4)
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
            elif show_headline and headline:
                head_font = _fit(
                    draw, headline, self.font_file, 84 if len(headline) < 40 else 68, text_box, 5
                )
                _draw_centered_block(
                    draw, headline, head_font, text_box, (255, 255, 255), stroke=3, max_lines=5
                )

            counter = spec.get("counter")
            if counter:
                cw = draw.textlength(str(counter), font=label_font) + 48
                ly = int(h * layout.LABEL_Y)
                draw.rounded_rectangle((w - 80 - cw, ly, w - 80, ly + 64), radius=32, fill=(20, 20, 20))
                draw.text((w - 80 - cw + 24, ly + 10), str(counter), font=label_font, fill=accent)

            label = spec.get("label")
            if label:
                lw = draw.textlength(label, font=label_font) + 48
                ly = int(h * layout.LABEL_Y)
                draw.rounded_rectangle((80, ly, 80 + lw, ly + 64), radius=32, fill=accent)
                draw.text((104, ly + 10), label, font=label_font, fill=(20, 20, 20))

            # Credits sit between the card text and the captions: the platform's own description and
            # comment bar cover the bottom of the frame, where they used to be.
            y = int(h * layout.CREDITS_Y)
            attribution = (spec.get("attribution") or "").strip()
            if attribution:
                for line in textwrap.wrap(attribution, width=56)[:2]:
                    draw.text(
                        (80, y),
                        line,
                        font=small_font,
                        fill=(210, 210, 210),
                        stroke_width=2,
                        stroke_fill=(0, 0, 0),
                    )
                    y += 36
            disclosure = (spec.get("disclosure") or "").strip()
            if disclosure:
                for line in textwrap.wrap(disclosure, width=56)[:2]:
                    draw.text(
                        (80, y),
                        line,
                        font=small_font,
                        fill=(190, 190, 190),
                        stroke_width=2,
                        stroke_fill=(0, 0, 0),
                    )
                    y += 36

            out_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(out_path, "PNG", optimize=False)
            return out_path

    def _render_layered(self, spec: dict[str, Any], out_path: Path, photo: Path | None) -> Path:
        """Full-frame layouts: a photo covering the whole frame (``full_bleed``), or, with no photo, a
        transparent layer the renderer lays over a video clip (``overlay``). Text sits in the top half on a
        dark gradient; the bottom band stays free for the burned captions, as on every card."""
        w, h = self.width, self.height
        hue = spec.get("palette_hue")
        _, _, accent = _palette(str(spec.get("seed", "aimz")), float(hue) if hue is not None else None)
        if photo is not None:
            try:
                canvas = _cover_fit(Image.open(photo).convert("RGB"), w, h).convert("RGBA")
            except Exception as exc:
                log.warning("could not use image %s: %s", photo, exc)
                canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        else:
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        for y in range(h):
            top = max(0.0, 1 - y / (h * 0.62)) * 0.70  # dark behind the headline and credits
            bottom = max(0.0, (y - h * 0.55) / (h * 0.45)) * 0.75  # dark behind the captions
            sd.line([(0, y), (w, y)], fill=(0, 0, 0, int(255 * max(top, bottom))))
        canvas = Image.alpha_composite(canvas, shade)
        draw = ImageDraw.Draw(canvas)
        headline = (spec.get("headline") or "").strip()
        if spec.get("kind") == "quote_card" and headline:
            headline = "\u201c" + headline + "\u201d"
        if headline and (
            bool(spec.get("show_headline", True)) or spec.get("kind") in {"stat_card", "quote_card"}
        ):
            size = 120 if spec.get("kind") == "stat_card" else 76
            box = (80, int(h * layout.CARD_TEXT_TOP), w - 80, int(h * layout.CARD_TEXT_BOTTOM))
            _draw_centered_block(
                draw,
                headline,
                _fit(draw, headline, self.font_file, size, box, 5),
                box,
                (255, 255, 255),
                stroke=3,
                max_lines=5,
            )
        label_font = _font(self.font_file, 36)
        label = spec.get("label")
        if label:
            lw = draw.textlength(label, font=label_font) + 48
            ly = int(h * layout.LABEL_Y)
            draw.rounded_rectangle((80, ly, 80 + lw, ly + 64), radius=32, fill=accent)
            draw.text((104, ly + 10), label, font=label_font, fill=(20, 20, 20))
        counter = spec.get("counter")
        if counter:
            cw = draw.textlength(str(counter), font=label_font) + 48
            ly = int(h * layout.LABEL_Y)
            draw.rounded_rectangle((w - 80 - cw, ly, w - 80, ly + 64), radius=32, fill=(20, 20, 20, 255))
            draw.text((w - 80 - cw + 24, ly + 10), str(counter), font=label_font, fill=accent)
        small_font = _font(self.font_file, 30)
        y = int(h * layout.CREDITS_Y)
        for text, fill in (
            (spec.get("attribution") or "", (220, 220, 220)),
            (spec.get("disclosure") or "", (190, 190, 190)),
        ):
            for line in textwrap.wrap(str(text).strip(), width=56)[:2]:
                draw.text((80, y), line, font=small_font, fill=fill, stroke_width=2, stroke_fill=(0, 0, 0))
                y += 36
        out_path.parent.mkdir(parents=True, exist_ok=True)
        (canvas if photo is None else canvas.convert("RGB")).save(out_path, "PNG")
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
