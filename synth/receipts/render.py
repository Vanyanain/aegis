"""Receipt rendering: genuine receipts and the physical pipeline that produces them.

A genuine receipt in the wild is not a clean render. It is a thermal print -- with per
character horizontal jitter and uneven ink density -- that was then PHOTOGRAPHED: warped by
perspective, lit unevenly, textured by paper fibre, blurred slightly, sampled through a
sensor and compressed by a phone's JPEG encoder, which stamps camera EXIF on the way out.

Every one of those steps leaves a statistical signature. Reproducing them faithfully is what
makes the forensics problem in aegis/sideb real rather than a trivially separable toy: a
detector that only learned "clean render vs photograph" would score beautifully here and
collapse on the first real photo of a real fake. So the genuine class gets the full physical
pipeline, and the fake families each diverge from it in a specific, documented way.

Indian retail context throughout: rupee amounts, GSTIN, CGST/SGST split, HSN codes.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Monospace faces stand in for thermal printer fonts; the proportional face is used by the
# template-forge family, whose giveaway is typography that is too clean for a thermal head.
MONO_FONTS = [
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]
FALLBACK_FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"

W = 620  # render width in px before the camera pipeline

MERCHANTS = [
    ("AEGISMART ELECTRONICS", "No 42, 100 Ft Road, Indiranagar, Bengaluru 560038", "29AABCA1234F1Z5"),
    ("AEGISMART APPAREL", "Shop 7, Linking Road, Bandra West, Mumbai 400050", "27AABCA1234F1Z3"),
    ("AEGISMART HOME", "12/A Park Street, Kolkata 700016", "19AABCA1234F1Z9"),
    ("AEGISMART BEAUTY", "Ground Flr, Sector 29, Gurugram 122001", "06AABCA1234F1Z1"),
    ("AEGISMART TRAVEL", "3rd Flr, Jubilee Hills, Hyderabad 500033", "36AABCA1234F1Z7"),
    ("AEGISMART GAMES", "Unit 5, Aundh, Pune 411007", "27AABCA1234F1Z8"),
    # Digital-services merchants issue tax invoices too, and their subscription rebills are
    # the single biggest source of first-party misuse. Every ledger category must have a
    # corresponding merchant here, or the merchant cross-check fires on genuine evidence
    # purely because the corpus had no store to match.
    ("AEGISMART SAAS", "WeWork Galaxy, Residency Road, Bengaluru 560025", "29AABCA1234F1Z2"),
    ("AEGISMART STREAM", "Tower B, DLF Cyber City, Gurugram 122002", "06AABCA1234F1Z4"),
]

ITEMS = {
    "AEGISMART ELECTRONICS": [("Wireless earbuds", 2999, "8518"), ("USB-C hub 7in1", 1899, "8471"),
                              ("Power bank 20000mAh", 2499, "8507"), ("Action camera", 12999, "8525"),
                              ("Mech keyboard TKL", 4599, "8471")],
    "AEGISMART APPAREL": [("Cotton kurta", 1299, "6205"), ("Running shoes", 3499, "6403"),
                          ("Denim jacket", 2799, "6201"), ("Linen shirt", 1899, "6205")],
    "AEGISMART HOME": [("Air purifier filter", 1799, "8421"), ("Ceramic dinner set", 3299, "6912"),
                       ("Memory foam pillow", 1499, "9404"), ("Table lamp", 999, "9405")],
    "AEGISMART BEAUTY": [("Vitamin C serum", 899, "3304"), ("Sunscreen SPF50", 649, "3304"),
                         ("Hair oil 200ml", 399, "3305"), ("Face cleanser", 549, "3304")],
    "AEGISMART TRAVEL": [("Hotel night Goa", 4500, "9963"), ("Airport transfer", 899, "9964"),
                         ("Travel insurance", 1200, "9971")],
    "AEGISMART GAMES": [("1200 game credits", 999, "9984"), ("Season pass", 1499, "9984"),
                        ("Cosmetic bundle", 599, "9984")],
    "AEGISMART SAAS": [("Team plan seat", 1499, "9984"), ("Pro plan monthly", 899, "9984"),
                       ("Storage add-on 100GB", 399, "9984"), ("API usage tier 2", 2499, "9984")],
    "AEGISMART STREAM": [("Monthly streaming plan", 299, "9984"), ("Annual plan", 2999, "9984"),
                         ("Premium tier upgrade", 499, "9984")],
}

GST_RATES = {"8518": 18, "8471": 18, "8507": 18, "8525": 18, "6205": 5, "6403": 12,
             "6201": 12, "8421": 18, "6912": 12, "9404": 12, "9405": 12, "3304": 18,
             "3305": 18, "9963": 12, "9964": 5, "9971": 18, "9984": 18}

PAY_MODES = ["VISA ****4471", "VISA ****8812", "UPI", "VISA ****2093"]


@dataclass
class ReceiptSpec:
    """The ground-truth content of a receipt, before it is drawn."""

    merchant: str
    address: str
    gstin: str
    invoice_no: str
    ts: datetime
    lines: list[tuple[str, int, float, str]]  # (name, qty, unit_price, hsn)
    pay_mode: str
    subtotal: float = 0.0
    cgst: float = 0.0
    sgst: float = 0.0
    total: float = 0.0
    # Bounding box of the rendered TOTAL amount, in flat-render coordinates. Populated by
    # render() so the digital-edit family can tamper with the actual number rather than a
    # guessed region of the page.
    total_bbox: tuple[int, int, int, int] | None = None
    total_font_path: str | None = None
    # Set when a family deliberately breaks the arithmetic. The forensic checker must
    # rediscover this from the image via OCR -- it never reads this field.
    arithmetic_broken: bool = False
    broken_fields: list[str] = field(default_factory=list)
    # How the file reached the merchant. Recorded for analysis only; the detector must infer
    # provenance from the file itself, never from this field.
    delivery: str = ""

    def compute(self) -> None:
        self.subtotal = round(sum(q * p for _, q, p, _ in self.lines), 2)
        tax = 0.0
        for _, q, p, hsn in self.lines:
            tax += q * p * GST_RATES.get(hsn, 18) / 100.0
        self.cgst = round(tax / 2, 2)
        self.sgst = round(tax / 2, 2)
        self.total = round(self.subtotal + self.cgst + self.sgst, 2)


def make_spec(
    rng: random.Random,
    ts: datetime | None = None,
    merchant_name: str | None = None,
) -> ReceiptSpec:
    """Build receipt content. `merchant_name` pins the store to a specific merchant.

    Pinning matters: a receipt supporting a dispute should be from the business that
    charged the card. Leaving the merchant random made the merchant cross-check fire on a
    third of GENUINE receipts, which is a corpus defect masquerading as a noisy rule.
    """
    if merchant_name:
        match = [m for m in MERCHANTS if m[0] == merchant_name]
        merchant, address, gstin = match[0] if match else rng.choice(MERCHANTS)
    else:
        merchant, address, gstin = rng.choice(MERCHANTS)
    pool = ITEMS[merchant]
    n = rng.randint(1, min(5, len(pool)))
    chosen = rng.sample(pool, n)
    lines = []
    for name, base, hsn in chosen:
        qty = rng.choices([1, 1, 1, 2, 3], k=1)[0]
        # Slight price dispersion so totals are not memorisable constants.
        price = round(base * rng.uniform(0.92, 1.08), 2)
        lines.append((name, qty, price, hsn))
    spec = ReceiptSpec(
        merchant=merchant,
        address=address,
        gstin=gstin,
        invoice_no=f"INV/{rng.randint(2025, 2026)}/{rng.randint(10000, 99999)}",
        ts=ts or datetime(2026, 1, 1) + timedelta(days=rng.randint(0, 200), hours=rng.randint(8, 22)),
        lines=lines,
        pay_mode=rng.choice(PAY_MODES),
    )
    spec.compute()
    return spec


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(FALLBACK_FONT, size)


def render(
    spec: ReceiptSpec,
    rng: random.Random,
    *,
    jitter: bool = True,
    font_path: str | None = None,
    ink_variation: bool = True,
) -> Image.Image:
    """Draw the receipt as a flat image.

    `jitter` and `ink_variation` model a thermal print head: characters land a fraction of a
    pixel off the baseline and ink density wanders. Turning them off produces the
    impossibly regular typography that characterises generated and template-forged receipts,
    which is exactly what the kerning/stroke features in Side B measure.
    """
    fp = font_path or rng.choice(MONO_FONTS)
    f_hdr = _font(fp, 26)
    f_sub = _font(fp, 15)
    f_body = _font(fp, 17)
    f_tot = _font(fp, 20)

    n_lines = len(spec.lines)
    h = 300 + n_lines * 52 + 220
    img = Image.new("L", (W, h), 252)
    d = ImageDraw.Draw(img)

    def text(xy, s, font, anchor=None, bold_pass=False):
        """Draw text with optional per-character jitter and ink density variation."""
        x, y = xy
        if not jitter:
            d.text((x, y), s, font=font, fill=28, anchor=anchor)
            if bold_pass:
                d.text((x + 0.6, y), s, font=font, fill=28, anchor=anchor)
            return
        if anchor and anchor.startswith("r"):
            x -= d.textlength(s, font=font)
        cx = x
        for ch in s:
            dy = rng.uniform(-0.65, 0.65)
            dx = rng.uniform(-0.35, 0.35)
            ink = rng.randint(18, 58) if ink_variation else 28
            # Thermal heads occasionally under-fire, leaving a faint character.
            if ink_variation and rng.random() < 0.012:
                ink = rng.randint(95, 140)
            d.text((cx + dx, y + dy), ch, font=font, fill=ink)
            if bold_pass:
                d.text((cx + dx + 0.6, y + dy), ch, font=font, fill=ink)
            cx += d.textlength(ch, font=font)

    y = 26
    hdr_x = W // 2 - d.textlength(spec.merchant, font=f_hdr) / 2
    text((hdr_x, y), spec.merchant, f_hdr, bold_pass=True)
    y += 38
    for chunk in _wrap(spec.address, 46):
        text((W // 2 - d.textlength(chunk, font=f_sub) / 2, y), chunk, f_sub)
        y += 20
    text((W // 2 - d.textlength(f"GSTIN: {spec.gstin}", font=f_sub) / 2, y), f"GSTIN: {spec.gstin}", f_sub)
    y += 30
    d.line([(24, y), (W - 24, y)], fill=90, width=1)
    y += 16

    text((24, y), spec.invoice_no, f_sub)
    text((W - 24, y), spec.ts.strftime("%d-%m-%Y %H:%M"), f_sub, anchor="ra")
    y += 30
    d.line([(24, y), (W - 24, y)], fill=140, width=1)
    y += 14

    text((24, y), "ITEM", f_sub)
    text((W - 200, y), "QTY", f_sub)
    text((W - 24, y), "AMOUNT", f_sub, anchor="ra")
    y += 26

    for name, qty, price, hsn in spec.lines:
        text((24, y), name[:30], f_body)
        y += 22
        text((40, y), f"HSN {hsn}  @ {price:,.2f}", f_sub)
        text((W - 200, y), str(qty), f_body)
        text((W - 24, y), f"{qty * price:,.2f}", f_body, anchor="ra")
        y += 30

    d.line([(24, y), (W - 24, y)], fill=140, width=1)
    y += 16
    for label, val in (("Subtotal", spec.subtotal), ("CGST", spec.cgst), ("SGST", spec.sgst)):
        text((24, y), label, f_body)
        text((W - 24, y), f"{val:,.2f}", f_body, anchor="ra")
        y += 26

    d.line([(24, y), (W - 24, y)], fill=90, width=2)
    y += 14
    text((24, y), "TOTAL", f_tot, bold_pass=True)
    total_str = f"INR {spec.total:,.2f}"
    tw = d.textlength(total_str, font=f_tot)
    spec.total_bbox = (int(W - 24 - tw - 6), int(y - 4), int(W - 20), int(y + 28))
    spec.total_font_path = fp
    text((W - 24, y), total_str, f_tot, anchor="ra", bold_pass=True)
    y += 40
    text((24, y), f"Paid by {spec.pay_mode}", f_body)
    y += 34
    msg = "Thank you for shopping with us"
    text((W // 2 - d.textlength(msg, font=f_sub) / 2, y), msg, f_sub)

    return img.convert("RGB")


def _wrap(s: str, n: int) -> list[str]:
    words, out, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


# --- The camera pipeline -----------------------------------------------------------------


def paper_texture(size: tuple[int, int], rng: random.Random) -> np.ndarray:
    """Low-frequency fibre texture. Real paper is never uniform."""
    w, h = size
    small = np.random.default_rng(rng.randint(0, 2**31)).normal(0, 1, (h // 8 + 1, w // 8 + 1))
    tex = np.array(Image.fromarray(small.astype(np.float32)).resize((w, h), Image.BICUBIC))
    return tex / (np.abs(tex).max() + 1e-6)


def photograph(img: Image.Image, rng: random.Random) -> Image.Image:
    """Warp, light, texture, blur and sample a flat render as if photographed.

    Order matters and mirrors physics: geometry first, then illumination and the physical
    medium, then optics, then the sensor. Applying noise before blur would produce a
    smoothed noise field no real sensor generates.
    """
    w, h = img.size

    # 1. Perspective: the phone is never square to the receipt.
    m = 0.045
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    dst = [
        (rng.uniform(0, w * m), rng.uniform(0, h * m)),
        (w - rng.uniform(0, w * m), rng.uniform(0, h * m)),
        (w - rng.uniform(0, w * m), h - rng.uniform(0, h * m)),
        (rng.uniform(0, w * m), h - rng.uniform(0, h * m)),
    ]
    img = img.transform((w, h), Image.PERSPECTIVE, _perspective_coeffs(dst, src),
                        Image.BICUBIC, fillcolor=(248, 247, 244))

    a = np.asarray(img).astype(np.float32)

    # 2. Illumination gradient: one side of the page is brighter than the other.
    yy, xx = np.mgrid[0:h, 0:w]
    ang = rng.uniform(0, 2 * np.pi)
    grad = (np.cos(ang) * (xx / w - 0.5) + np.sin(ang) * (yy / h - 0.5))
    a *= (1.0 + rng.uniform(0.06, 0.20) * grad)[:, :, None]

    # 3. Paper fibre.
    a += (paper_texture((w, h), rng) * rng.uniform(2.0, 6.0))[:, :, None]

    # 4. Slight warm/cool cast from ambient light.
    a *= np.array([rng.uniform(0.98, 1.03), 1.0, rng.uniform(0.97, 1.02)])[None, None, :]

    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    # 5. Optics: mild defocus.
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.35, 0.95)))

    # 6. Sensor noise, applied last, before compression.
    a = np.asarray(img).astype(np.float32)
    a += np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(1.6, 4.2), a.shape)
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    # 7. Handheld capture is rarely perfectly upright.
    img = img.rotate(rng.uniform(-1.6, 1.6), resample=Image.BICUBIC, fillcolor=(248, 247, 244))
    return img


def _perspective_coeffs(src, dst):
    matrix = []
    for (x, y), (X, Y) in zip(src, dst):
        matrix.append([x, y, 1, 0, 0, 0, -X * x, -X * y])
        matrix.append([0, 0, 0, x, y, 1, -Y * x, -Y * y])
    A = np.array(matrix, dtype=float)
    B = np.array(dst, dtype=float).reshape(8)
    return np.linalg.solve(A, B).tolist()


CAMERAS = [
    ("Apple", "iPhone 14"), ("Apple", "iPhone 15 Pro"), ("samsung", "SM-S918B"),
    ("Xiaomi", "23021RAAEG"), ("OnePlus", "CPH2447"), ("realme", "RMX3771"),
]


# How the evidence actually reaches the merchant. This matters enormously and is the
# difference between a corpus that teaches something and one that teaches a shortcut.
#
# In a first draft of this corpus every genuine receipt carried full camera EXIF and every
# fake carried none, so `exif_present` separated the classes perfectly and the detector
# learned nothing else -- arithmetic, typography, compression and noise all had exactly zero
# feature importance. That model would have collapsed the moment a forger ran exiftool.
#
# Reality: most dispute evidence arrives through a channel that destroys metadata. WhatsApp
# strips EXIF outright, and in India it is the dominant channel for sending a photo of a
# receipt. Screenshots have no camera EXIF by construction. Email clients resize and strip.
# Meanwhile a forger who wants camera EXIF simply copies it from a real photo.
#
# So the delivery channel is drawn INDEPENDENTLY of authenticity, and a share of fakes carry
# fabricated camera EXIF. Metadata stays informative -- it is genuinely a useful signal --
# but it can no longer decide the case on its own.
DELIVERY_CHANNELS = ("direct_photo", "whatsapp", "screenshot", "email_resize")
DELIVERY_P = (0.40, 0.30, 0.15, 0.15)

# Fraction of never-photographed fake evidence carrying fabricated camera EXIF. Tuned so
# that camera metadata is present at a similar rate in both classes: copying EXIF from a
# real photo is a one-command operation, so its presence should be weak evidence of
# authenticity, not strong evidence. Setting this too low recreates the shortcut in reverse.
FABRICATE_EXIF_P = 0.40


def _camera_exif(rng: random.Random, ts: datetime) -> bytes:
    make, model = rng.choice(CAMERAS)
    exif = Image.Exif()
    exif[271] = make                      # Make
    exif[272] = model                     # Model
    exif[305] = f"{model} {rng.choice(['16.5', '17.1', '14.0'])}"  # Software
    exif[306] = ts.strftime("%Y:%m:%d %H:%M:%S")                   # DateTime
    exif[36867] = ts.strftime("%Y:%m:%d %H:%M:%S")                 # DateTimeOriginal
    exif[34855] = rng.choice([50, 64, 100, 200, 400])               # ISOSpeedRatings
    exif[33437] = (rng.choice([18, 20, 22]), 10)                    # FNumber
    return exif.tobytes()


def save_evidence(
    img: Image.Image,
    path: Path,
    rng: random.Random,
    ts: datetime,
    *,
    delivery: str | None = None,
    fabricate_exif: bool = False,
    is_photograph: bool = True,
) -> str:
    """Write the file as it would arrive after travelling through `delivery`.

    Returns the channel used. Camera EXIF survives only the direct-photo path, or is
    fabricated outright. Each channel also has its own recompression behaviour, so the
    compression features see realistic variety rather than one clean signature per class.
    """
    delivery = delivery or rng.choices(DELIVERY_CHANNELS, weights=DELIVERY_P, k=1)[0]

    # An image that was never inside a camera cannot inherit camera EXIF from the delivery
    # path, however it travelled. It can only have EXIF that someone fabricated. Genuine
    # photographs, recycled photographs and edited photographs all WERE photographed, so
    # they keep the direct-photo metadata; generated and template-forged images do not.
    if delivery == "direct_photo":
        quality, sub = rng.randint(74, 92), rng.choice([1, 2])
        exif = _camera_exif(rng, ts) if (is_photograph or fabricate_exif) else b""
    elif delivery == "whatsapp":
        # Re-encoded by the messenger: lower quality, second compression, no metadata.
        quality, sub = rng.randint(62, 80), 2
        exif = _camera_exif(rng, ts) if fabricate_exif else b""
    elif delivery == "screenshot":
        quality, sub = rng.randint(85, 95), 0
        exif = _camera_exif(rng, ts) if fabricate_exif else b""
    else:  # email_resize
        quality, sub = rng.randint(70, 88), rng.choice([1, 2])
        exif = _camera_exif(rng, ts) if fabricate_exif else b""

    if delivery in ("whatsapp", "email_resize"):
        # These channels downscale. Resampling perturbs the noise floor and the JPEG grid,
        # which is exactly the kind of variation a real corpus contains.
        k = rng.uniform(0.72, 0.95)
        img = img.resize((max(64, int(img.width * k)), max(64, int(img.height * k))), Image.LANCZOS)

    img.save(path, "JPEG", quality=quality, subsampling=sub, exif=exif)
    return delivery


def save_photo(img: Image.Image, path: Path, rng: random.Random, ts: datetime) -> str:
    """Photographed evidence, delivered through a randomly drawn channel."""
    return save_evidence(img, path, rng, ts)


def save_clean(img: Image.Image, path: Path, quality: int = 95) -> None:
    """Write with no EXIF at all. Kept for callers that do not model delivery."""
    img.save(path, "JPEG", quality=quality, subsampling=0)


def to_bytes(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def fit_to_amount(spec: ReceiptSpec, target: float) -> ReceiptSpec:
    """Rescale unit prices so the receipt total lands on `target`.

    Used when a receipt is meant to corroborate a specific transaction. A fraudster
    fabricating proof makes the number match the charge they are disputing -- so for the
    generated and template-forged families the ledger cross-check is CONSISTENT, and the
    fake must be caught on document evidence alone. Only the families that cannot match
    (an inflated edit, a receipt recycled from another order) fail the cross-check. That
    separation is deliberate: it stops any single feature from solving every family.
    """
    spec.compute()
    if spec.total <= 0:
        return spec
    k = target / spec.total
    spec.lines = [(n, q, round(p * k, 2), h) for n, q, p, h in spec.lines]
    spec.compute()
    return spec
