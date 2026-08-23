"""The four fake-evidence families.

Each family is a DETECTION TARGET. They are generated offline, used only to train and
evaluate the Side B detector, and never exposed through the API. Nothing here is an attack
capability: these are the standard doctored-document classes that any document-forensics
system must be trained against, reproduced so the detector has something honest to learn.

    ai_generated    Text and layout produced whole, with the arithmetic inconsistency that
                    characterises image generators treating numbers as visual tokens.
                    Typography is impossibly regular; no camera ever touched the file.

    digital_edit    A GENUINE photographed receipt whose total has been altered and the file
                    re-saved. The edited region carries a different compression history from
                    its surroundings -- the classic ELA and double-quantisation signature.

    template_forge  Produced from a fixed generator template. Arithmetic is usually correct
                    (the generator computes it), but layout geometry is pixel-identical
                    across samples, so a layout hash collides with known templates.

    recycled        A GENUINE, UNALTERED receipt from a DIFFERENT REAL TRANSACTION, submitted
                    as proof for this dispute. Forensically perfect. No pixel-level method
                    can ever flag it. It is caught only by cross-checking the receipt against
                    the transaction record -- which is why this class exists in AEGIS and in
                    no expense-audit tool: those tools have no ledger to check against.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from synth.receipts.render import (
    FABRICATE_EXIF_P, MONO_FONTS, ReceiptSpec, fit_to_amount, make_spec, photograph,
    render, save_evidence,
)

FAMILIES = ("genuine", "ai_generated", "digital_edit", "template_forge", "recycled")
FAKE_FAMILIES = ("ai_generated", "digital_edit", "template_forge", "recycled")

# A single fixed font + fixed seed produce the pixel-identical geometry that defines the
# template-forge family.
TEMPLATE_FONT = MONO_FONTS[0]


def make_genuine(rng: random.Random, out: Path, spec: ReceiptSpec | None = None) -> ReceiptSpec:
    spec = spec or make_spec(rng)
    img = photograph(render(spec, rng, jitter=True, ink_variation=True), rng)
    spec.delivery = save_evidence(img, out, rng, spec.ts)
    return spec


def make_ai_generated(
    rng: random.Random, out: Path, target: float | None = None,
    ts: datetime | None = None, merchant_name: str | None = None,
) -> ReceiptSpec:
    """Generated whole: broken internal arithmetic, flawless typography, no camera provenance.

    `target` is the amount actually charged. A forger fabricating proof for a specific
    dispute makes the HEADLINE TOTAL match that charge -- there would be no point otherwise.
    So the total is pinned to the claim and the inconsistency is pushed inside the document,
    where line items no longer sum to the subtotal or the tax no longer reconciles. That is
    both the realistic behaviour and the documented failure mode of image generators, which
    treat numerals as visual tokens rather than quantities: arithmetic errors appear in
    97.2% of real AI-generated receipts while the visible total looks perfectly plausible.

    Keeping the total honest here is also what stops the ledger cross-check from trivially
    solving this family, which would make the forensic features look far better than they are.
    """
    spec = make_spec(rng, ts=ts, merchant_name=merchant_name)
    if target is not None:
        fit_to_amount(spec, target)

    pinned_total = spec.total
    spec.arithmetic_broken = True
    mode = rng.random()
    if mode < 0.45:
        # Line items no longer sum to the printed subtotal.
        drift = rng.choice([-1, 1]) * rng.uniform(0.015, 0.07)
        spec.subtotal = round(spec.subtotal * (1 + drift), 2)
        spec.broken_fields.append("subtotal")
    elif mode < 0.78:
        # Tax does not reconcile against the subtotal at any legal GST rate.
        f = rng.uniform(0.55, 0.85) if rng.random() < 0.5 else rng.uniform(1.15, 1.5)
        spec.cgst = round(spec.cgst * f, 2)
        spec.sgst = round(spec.sgst * f, 2)
        spec.broken_fields.append("tax")
    else:
        # Both components drift; the total still does not equal their sum.
        spec.subtotal = round(spec.subtotal * rng.uniform(0.96, 1.04), 2)
        spec.cgst = round(spec.cgst * rng.uniform(0.9, 1.1), 2)
        spec.sgst = round(spec.sgst * rng.uniform(0.9, 1.1), 2)
        spec.broken_fields.append("subtotal_and_tax")

    # The printed total stays on the claimed amount, so subtotal + tax != total.
    spec.total = pinned_total

    # No jitter, no ink variation: generated glyphs are perfectly placed and uniformly dark.
    img = render(spec, rng, jitter=False, ink_variation=False)

    # Generators emit a synthetic image, not a photograph: a faint uniform noise field, a
    # high-quality single-pass save, and no camera EXIF whatsoever.
    a = np.asarray(img).astype(np.float32)
    a += np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(0.4, 1.1), a.shape)
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    spec.delivery = save_evidence(
        img, out, rng, spec.ts, is_photograph=False,
        fabricate_exif=rng.random() < FABRICATE_EXIF_P,
    )
    return spec


def make_digital_edit(
    rng: random.Random, out: Path, target: float | None = None,
    ts: datetime | None = None, merchant_name: str | None = None,
) -> ReceiptSpec:
    """A genuine photograph whose TOTAL was repainted, then re-saved.

    The sequence mirrors what someone actually does: photograph the receipt, open the photo,
    paint over the number, export again. That ordering is what creates the forensic
    signature -- the whole image carries one JPEG compression history, while the repainted
    region carries none until the final save. Error-level analysis reads that discontinuity.
    Doing the edit before the photograph instead would let the camera pipeline's blur and
    resampling wash the evidence away, and the family would be undetectable for the wrong
    reason.
    """
    spec = make_spec(rng, ts=ts, merchant_name=merchant_name)
    if target is not None:
        fit_to_amount(spec, target)
    true_total = spec.total

    flat = render(spec, rng, jitter=True, ink_variation=True)
    bbox = spec.total_bbox
    photo = photograph(flat, rng)

    # Save and reload: this is "the photo as it sat on the phone", compression history #1.
    tmp = out.with_suffix(".tmp.jpg")
    save_evidence(photo, tmp, rng, spec.ts, delivery="direct_photo")
    edited = Image.open(tmp).convert("RGB")
    tmp.unlink(missing_ok=True)

    # Inflate the claim. The receipt will now disagree with the transaction record too,
    # so this family is catchable both forensically and by ledger cross-check.
    spec.total = round(true_total * rng.uniform(1.25, 2.6), 2)
    spec.arithmetic_broken = True
    spec.broken_fields.append("total_edited")

    if bbox is None:
        spec.delivery = save_evidence(edited, out, rng, spec.ts)
        return spec

    # The camera pipeline warps and rotates mildly, so pad the target region rather than
    # assuming the number sits exactly where the flat render put it.
    x0, y0, x1, y1 = bbox
    pad = 6
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(edited.width, x1 + pad), min(edited.height, y1 + pad)

    # The patch has to be blank paper that still matches the photograph's lighting. Lifting
    # a horizontal strip from above would drag neighbouring text into the patch, so instead
    # sample the always-blank LEFT MARGIN at the same rows: that gives the correct paper
    # level for this lighting band, per row, with nothing printed on it.
    margin = np.asarray(edited.crop((2, y0, 22, y1))).astype(np.float32)
    per_row = np.median(margin, axis=1)                       # (h, 3) paper level by row
    cover = np.repeat(per_row[:, None, :], x1 - x0, axis=1)
    cover += np.random.default_rng(rng.randint(0, 2**31)).normal(0, 2.0, cover.shape)
    edited.paste(Image.fromarray(np.clip(cover, 0, 255).astype(np.uint8)), (x0, y0))

    # Repaint the inflated total in the same face and size as the original, at an ink
    # density sampled from the surviving text elsewhere on the page.
    d = ImageDraw.Draw(edited)
    try:
        font = ImageFont.truetype(spec.total_font_path or MONO_FONTS[0], 20)
    except OSError:
        font = ImageFont.load_default()
    new_str = f"INR {spec.total:,.2f}"
    tw = d.textlength(new_str, font=font)
    ink = int(np.percentile(np.asarray(edited.convert("L")), 2)) + rng.randint(0, 12)
    tx, ty = x1 - pad - tw, y0 + pad
    d.text((tx, ty), new_str, font=font, fill=(ink, ink, ink))
    d.text((tx + 0.6, ty), new_str, font=font, fill=(ink, ink, ink))

    # Feather the patch so there is no hard seam, then re-noise it to the local sensor
    # noise level. Without this the region would be suspiciously smooth.
    region = np.asarray(
        edited.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.8)))
    ).astype(np.float32)
    region += np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(1.5, 3.0), region.shape)
    edited.paste(Image.fromarray(np.clip(region, 0, 255).astype(np.uint8)), (x0, y0))

    # Final save: compression history #2 over everything except the repainted pixels.
    spec.delivery = save_evidence(edited, out, rng, spec.ts)
    return spec


def make_template_forge(
    rng: random.Random, out: Path, target: float | None = None,
    ts: datetime | None = None, merchant_name: str | None = None,
) -> ReceiptSpec:
    """Built from a fixed generator template: correct maths, identical geometry.

    Like the generated family, the total is pinned to the amount being claimed. Unlike it,
    the arithmetic underneath is CORRECT -- a template generator computes it properly. So
    both the ledger cross-check and the arithmetic check are blind to this family by
    construction, and it can only be caught on typography and layout geometry. That is the
    point: it forces the detector to be more than an arithmetic validator.
    """
    spec = make_spec(rng, ts=ts, merchant_name=merchant_name)
    if target is not None:
        fit_to_amount(spec, target)
    # Arithmetic is right -- a template generator computes it. So arithmetic features are
    # useless here by construction, and the family must be caught on typography and layout.
    # This is deliberate: it stops the detector from becoming a one-feature arithmetic check.
    img = render(spec, rng, jitter=False, font_path=TEMPLATE_FONT, ink_variation=False)

    # A light, uniform, always-identical post-process. No perspective, no lighting gradient.
    a = np.asarray(img).astype(np.float32)
    a += np.random.default_rng(12345).normal(0, 0.8, a.shape)  # FIXED seed: same noise field every time
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    spec.delivery = save_evidence(
        img, out, rng, spec.ts, is_photograph=False,
        fabricate_exif=rng.random() < FABRICATE_EXIF_P,
    )
    return spec


def make_recycled(rng: random.Random, out: Path, ts_offset_days: int = 0) -> ReceiptSpec:
    """A genuine receipt from a different transaction. The image itself is not fake.

    Returned spec describes what the receipt ACTUALLY shows. The caller pairs it with a
    different transaction record, and the mismatch -- not the pixels -- is the evidence.
    """
    spec = make_spec(rng)
    if ts_offset_days:
        spec.ts = spec.ts + timedelta(days=ts_offset_days)
    img = photograph(render(spec, rng, jitter=True, ink_variation=True), rng)
    spec.delivery = save_evidence(img, out, rng, spec.ts)
    return spec
