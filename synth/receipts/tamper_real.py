"""Tampered-evidence families built on REAL receipt photographs.

WHY THIS REPLACED THE RENDERED CORPUS.

The first version of Side B rendered its own receipts and then forged them. Both classes
came out of the same generator, so the detector was scored on its ability to invert a
process we wrote. Impressive numbers, no external meaning.

Here the base documents are real: 1,973 photographs from CORD (Naver Clova, Indonesian
retail) and SROIE (ICDAR 2019 Robust Reading Challenge). Real cameras, real thermal print,
real lighting, real skew, real JPEG histories, real OCR difficulty. Only the manipulation is
programmatic -- which is exactly the methodology of the benchmark this problem is measured
against: DocTamper (CVPR 2023) is described by its authors as synthetic manipulations
applied to photographed documents, and AIForge-Doc (2026) builds its forgeries on CORD,
SROIE and WildReceipt in the same way.

The manipulations target the real annotated regions rather than guessed coordinates. CORD
ships word-level quadrilaterals for `total.total_price`, `sub_total.*` and `menu.price`, so
a tamper lands on the actual total the way a real forger's would.

    genuine       The photograph, untouched.
    copy_move     Digits lifted from a price elsewhere on the SAME receipt and pasted over
                  the total. This is what receipt forgery actually looks like, and it leaves
                  a duplicated-region signature plus a second compression pass.
    splice        A region from a DIFFERENT real receipt pasted over the total. Foreign
                  sensor noise, foreign lighting, foreign quantisation history.
    recycled      The photograph, untouched, submitted against a different transaction.
                  Forensically perfect; catchable only against the ledger.

Nothing here is offence-capable: these are the standard manipulation classes any document
forensics system must be trained against, produced offline for the detector and never
reachable from the serving API.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

FAMILIES = ("genuine", "copy_move", "splice", "recycled")
TAMPERED_FAMILIES = ("copy_move", "splice")


@dataclass
class RealReceipt:
    """A real receipt photograph plus whatever ground truth its dataset provides."""

    image: Image.Image
    source: str                      # "cord" | "sroie"
    total: float | None = None
    subtotal: float | None = None
    tax: float | None = None
    service: float | None = None    # service charge, common on real restaurant receipts
    rounding: float | None = None   # rounding / discount line
    line_items: list[float] = field(default_factory=list)
    total_box: tuple[int, int, int, int] | None = None
    price_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)


def _num(s: str) -> float | None:
    """Parse a receipt money string. CORD is Indonesian: '1,591,600' means 1591600."""
    if not s:
        return None
    clean = "".join(c for c in str(s) if c.isdigit() or c in ".,")
    if not clean:
        return None
    # Indonesian receipts use ',' and '.' as thousands separators, not decimals.
    clean = clean.replace(",", "").replace(".", "")
    try:
        return float(clean)
    except ValueError:
        return None


def _quad_box(words: list[dict]) -> tuple[int, int, int, int] | None:
    xs, ys = [], []
    for w in words:
        q = w.get("quad") or {}
        for k in ("x1", "x2", "x3", "x4"):
            if k in q:
                xs.append(q[k])
        for k in ("y1", "y2", "y3", "y4"):
            if k in q:
                ys.append(q[k])
    if not xs or not ys:
        return None
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


def parse_cord(example: dict[str, Any]) -> RealReceipt:
    """Read one CORD record into a RealReceipt, using its real annotations."""
    gt = json.loads(example["ground_truth"])
    img = example["image"].convert("RGB")
    r = RealReceipt(image=img, source="cord")

    parse = gt.get("gt_parse", {})
    tot = parse.get("total") or {}
    r.total = _num(tot.get("total_price")) if isinstance(tot, dict) else None
    sub = parse.get("sub_total") or {}
    if isinstance(sub, dict):
        r.subtotal = _num(sub.get("subtotal_price"))
        r.tax = _num(sub.get("tax_price"))
        r.service = _num(sub.get("service_price"))
        # `etc` carries rounding adjustments, which are frequently negative.
        etc_raw = sub.get("etc")
        r.rounding = _num(etc_raw)
        if r.rounding is not None and isinstance(etc_raw, str) and "-" in etc_raw:
            r.rounding = -r.rounding

    menu = parse.get("menu") or []
    if isinstance(menu, dict):
        menu = [menu]
    for m in menu:
        if isinstance(m, dict):
            v = _num(m.get("price"))
            if v:
                r.line_items.append(v)

    for line in gt.get("valid_line", []):
        box = _quad_box(line.get("words", []))
        if box is None:
            continue
        cat = line.get("category", "")
        if cat == "total.total_price":
            r.total_box = box
        elif cat in ("menu.price", "sub_total.subtotal_price", "sub_total.tax_price"):
            r.price_boxes.append(box)
    return r


def parse_sroie(example: dict[str, Any]) -> RealReceipt:
    """SROIE ships images and OCR text; annotations vary by mirror, so treat it as
    image-only and let the forensic OCR layer read it like any unseen document."""
    img = example["image"] if isinstance(example.get("image"), Image.Image) else None
    if img is None:
        raise ValueError("no image")
    return RealReceipt(image=img.convert("RGB"), source="sroie")


# --- manipulations ---------------------------------------------------------------


def _target_box(r: RealReceipt, rng: random.Random) -> tuple[int, int, int, int]:
    """Where to tamper: the annotated total if we have one, else the lower-right region
    where a total sits on essentially every receipt layout."""
    if r.total_box:
        return r.total_box
    w, h = r.image.size
    y0 = int(h * rng.uniform(0.70, 0.86))
    return (int(w * 0.55), y0, int(w * 0.95), min(h, y0 + max(14, int(h * 0.028))))


def _paste(dst: Image.Image, patch: Image.Image, box, rng: random.Random) -> None:
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    # Only correct a mild size difference. Stretching a small source box across a large
    # total box produces a blurred, upscaled block that any detector -- and any human --
    # spots instantly, which would make the family trivially separable for the wrong
    # reason. A real forger transplants digits at their native scale.
    pw, ph = patch.size
    if abs(pw - w) / max(w, 1) > 0.25 or abs(ph - h) / max(h, 1) > 0.25:
        patch = patch.resize((w, h), Image.LANCZOS) if pw < w * 0.5 else patch.crop((0, 0, min(pw, w), min(ph, h)))
        if patch.size != (w, h):
            base = Image.new("RGB", (w, h), tuple(np.asarray(patch).reshape(-1, 3).mean(0).astype(int)))
            base.paste(patch, (0, 0))
            patch = base
    else:
        patch = patch.resize((w, h), Image.LANCZOS)
    # Feather and re-noise so the seam is not a trivially detectable hard edge; a careless
    # forgery would be caught by any method, which would flatter the detector.
    patch = patch.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.5)))
    a = np.asarray(patch).astype(np.float32)
    a += np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(0.8, 2.0), a.shape)
    dst.paste(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), (x0, y0))


def make_copy_move(r: RealReceipt, rng: random.Random) -> Image.Image:
    """Lift digits from another price on the same receipt and paste them over the total."""
    img = r.image.copy()
    box = _target_box(r, rng)
    # Prefer a source region whose dimensions already match the target, so the digits keep
    # their native scale and no resampling blur is introduced.
    tw, th = box[2] - box[0], box[3] - box[1]
    src_boxes = [b for b in r.price_boxes if b != r.total_box]
    src_boxes.sort(key=lambda b: abs((b[2] - b[0]) - tw) + abs((b[3] - b[1]) - th))
    if src_boxes and abs((src_boxes[0][2] - src_boxes[0][0]) - tw) < tw * 0.4:
        sb = src_boxes[0]
        # Widen the source crop to the target size around its own centre rather than scaling.
        cx, cy = (sb[0] + sb[2]) // 2, (sb[1] + sb[3]) // 2
        sx0 = max(0, min(r.image.width - tw, cx - tw // 2))
        sy0 = max(0, min(r.image.height - th, cy - th // 2))
        patch = r.image.crop((sx0, sy0, sx0 + tw, sy0 + th))
    elif src_boxes:
        sb = rng.choice(src_boxes)
        cx, cy = (sb[0] + sb[2]) // 2, (sb[1] + sb[3]) // 2
        sx0 = max(0, min(r.image.width - tw, cx - tw // 2))
        sy0 = max(0, min(r.image.height - th, cy - th // 2))
        patch = r.image.crop((sx0, sy0, sx0 + tw, sy0 + th))
    else:
        # No annotations: lift a same-sized strip from higher up the same column, which is
        # where other numerals live on any receipt.
        x0, y0, x1, y1 = box
        dy = max(20, (y1 - y0) * rng.randint(3, 8))
        sy = max(0, y0 - dy)
        patch = r.image.crop((x0, sy, x1, sy + (y1 - y0)))
    _paste(img, patch, box, rng)
    return img


def make_splice(r: RealReceipt, donor: RealReceipt, rng: random.Random) -> Image.Image:
    """Paste a region from a DIFFERENT real receipt over the total."""
    img = r.image.copy()
    box = _target_box(r, rng)
    dbox = _target_box(donor, rng) if donor.total_box else None
    if dbox is None:
        dw, dh = donor.image.size
        y0 = int(dh * rng.uniform(0.6, 0.85))
        dbox = (int(dw * 0.5), y0, int(dw * 0.95), min(dh, y0 + max(14, int(dh * 0.03))))
    _paste(img, donor.image.crop(dbox), box, rng)
    return img


def save(img: Image.Image, path, rng: random.Random, second_pass: bool) -> None:
    """Write the evidence file.

    Tampered images get a second compression pass, because the manipulation happened after
    the original photograph was already encoded -- that mismatch between the edited region's
    compression history and its surroundings is what error-level analysis reads. Genuine
    images are written once at photo quality.
    """
    q = rng.randint(78, 94) if second_pass else rng.randint(85, 96)
    img.save(path, "JPEG", quality=q, subsampling=rng.choice([1, 2]))


def arithmetic_truth(r: RealReceipt) -> dict[str, float | bool | None]:
    """Whether the REAL receipt actually reconciles, from its own annotations.

    Used to measure how reliable the arithmetic layer is on real documents -- real receipts
    include service charges, rounding lines and discounts, so a naive
    `items == subtotal == total` check does not hold universally and it is better to know
    that than to assume it.
    """
    items_sum = sum(r.line_items) if r.line_items else None
    recon = None
    if r.subtotal and r.total:
        # Real receipts are not `subtotal + tax = total`. They carry service charges,
        # rounding adjustments and discounts as separate lines. The first CORD receipt
        # inspected showed a 6.3% "error" that was simply a 100,950 service charge the
        # naive check ignored. A rule that fires on that would flag most genuine
        # restaurant receipts as fabricated.
        expected = r.subtotal + (r.tax or 0.0) + (r.service or 0.0) + (r.rounding or 0.0)
        recon = abs(expected - r.total) / max(r.total, 1.0)
    return {
        "total": r.total,
        "subtotal": r.subtotal,
        "tax": r.tax,
        "service": r.service,
        "rounding": r.rounding,
        "n_items": len(r.line_items),
        "items_sum": items_sum,
        "items_vs_subtotal_rel": (
            abs(items_sum - r.subtotal) / max(r.subtotal, 1.0)
            if items_sum and r.subtotal else None
        ),
        "components_vs_total_rel": recon,
    }
