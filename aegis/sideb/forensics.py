"""Side B forensic feature extraction.

Ordered by how much a human can act on the result, not by how fashionable the method is:

  1. ARITHMETIC     Does the document add up? Line items -> subtotal -> tax -> total.
                    This leads because it is both the dominant empirical signal (arithmetic
                    errors appear in 97.2% of AI-generated receipts) and the most
                    explainable evidence possible. "Line items sum to Rs 4,180 but the
                    printed subtotal is Rs 4,240" is a sentence an issuer, a merchant and
                    an Indian court can each read without trusting a model.

  2. PROVENANCE     EXIF: was this file ever inside a camera? Generated images carry no
                    Make/Model and no capture timestamp.

  3. COMPRESSION    JPEG quantisation tables and error-level analysis. A region pasted into
                    an already-compressed photograph has a different compression history
                    from its surroundings, and ELA renders that discontinuity visible.

  4. TYPOGRAPHY     Thermal print heads jitter; generated glyphs do not. Baseline drift,
                    glyph-height dispersion and OCR confidence separate print from render.

  5. NOISE          Sensor noise is spatially structured; synthetic noise is uniform.

  6. CROSS-CHECK    Does the receipt agree with the transaction it is submitted against?
                    Amount, date, merchant. This is the layer no expense-audit tool has,
                    because it requires the ledger -- and it is the only thing that can
                    catch a genuine receipt recycled from a different order.

Every feature is a plain number with a name, so a verdict can always be narrated. That is a
requirement, not a nicety: an unexplainable verdict is inadmissible under Section 63 BSA.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

try:
    import pytesseract
    HAS_OCR = True
except ImportError:  # pragma: no cover
    HAS_OCR = False

# Legal Indian GST slabs. A blended basket lands between these, never outside them.
GST_SLABS = (0.0, 5.0, 12.0, 18.0, 28.0)

# Ceiling on how far a total may exceed its subtotal. The worst realistic case is a high
# tax slab stacked on a service charge (28% GST, or 11% PB1 + 10% service in Indonesia,
# plus rounding). Anything beyond this is not a tax, it is a different number.
MAX_UPLIFT = 0.35

# Money on a real receipt is not always "1,234.56".
#
# The first version of this pattern required two decimal places, which silently matched
# NOTHING on the CORD corpus: Indonesian receipts write amounts as "20,000" and
# "1,591,600" with no decimal part at all. The arithmetic layer -- the headline feature --
# parsed 0.04 of its fields on real documents while appearing to work perfectly on the
# rendered corpus, because the rendered corpus was written in the one format the regex knew.
#
# This accepts grouped amounts with or without a fractional part, in either separator
# convention, and `_money` below decides which convention a given string is using.
MONEY = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\d])")


@dataclass
class OcrResult:
    text: str
    words: list[dict[str, Any]]


def read_ocr(img: Image.Image) -> OcrResult:
    """Run OCR ONCE and derive both the text and the word geometry from the same pass.

    Calling image_to_string and image_to_data separately runs Tesseract twice and doubles
    the cost of the slowest stage in the pipeline. Reconstructing the text from the word
    boxes is not just cheaper -- it guarantees the arithmetic layer and the typography layer
    are reading exactly the same recognition, so a verdict can never cite a number that the
    geometry says was never there.
    """
    if not HAS_OCR:
        return OcrResult("", [])
    g = img.convert("L")
    try:
        data = pytesseract.image_to_data(g, config="--psm 6", output_type=pytesseract.Output.DICT)
    except Exception:
        return OcrResult("", [])

    words: list[dict[str, Any]] = []
    lines: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for i in range(len(data["text"])):
        t = data["text"][i].strip()
        conf = float(data["conf"][i])
        if not t or conf < 0:
            continue
        w = {
            "text": t, "conf": conf, "left": data["left"][i], "top": data["top"][i],
            "width": data["width"][i], "height": data["height"][i],
            "line": data["line_num"][i], "block": data["block_num"][i],
        }
        words.append(w)
        lines.setdefault((w["block"], data["par_num"][i], w["line"]), []).append(w)

    text = "\n".join(
        " ".join(w["text"] for w in sorted(ws, key=lambda w: w["left"]))
        for _, ws in sorted(lines.items())
    )
    return OcrResult(text, words)


def _money(tok: str) -> float | None:
    """Interpret one matched token, resolving the separator convention.

    A trailing group of THREE digits after a separator is a thousands group ("20,000" and
    "20.000" are both twenty thousand). A trailing group of one or two digits is a
    fractional part ("12.50", "12,50"). Anything else is read as a plain integer. Guessing
    wrong here is a factor-of-1000 error on the total, so it is resolved explicitly rather
    than by assuming a locale.
    """
    tok = tok.strip()
    if not tok:
        return None
    last_sep = max(tok.rfind("."), tok.rfind(","))
    if last_sep == -1:
        try:
            return float(tok)
        except ValueError:
            return None
    tail = tok[last_sep + 1:]
    head = tok[:last_sep].replace(",", "").replace(".", "")
    try:
        if len(tail) == 3:                 # thousands group
            return float(head + tail)
        if len(tail) in (1, 2):            # fractional part
            return float(f"{head}.{tail}")
        return float(tok.replace(",", "").replace(".", ""))
    except ValueError:
        return None


def _num(s: str) -> float | None:
    """Last monetary value on a line.

    Deliberately does NOT strip whitespace first: on an item line reading
    "HSN 9963 @ 550.68  1  550.68" the spaces are the only thing separating the quantity
    from the amount, and removing them yields the phantom value 1550.68.
    """
    m = MONEY.findall(s)
    for tok in reversed(m):
        v = _money(tok)
        # Quantities and line numbers also match the pattern; a receipt total is not 1.
        if v is not None and v >= 10:
            return v
    return _money(m[-1]) if m else None


def _edit_le1(a: str, b: str) -> bool:
    """True if `a` is within one substitution of `b` (same length)."""
    if len(a) != len(b):
        return False
    return sum(x != y for x, y in zip(a, b)) <= 1


# Receipts are not written in one language. The corpus spans Indian (CGST/SGST), Indonesian
# (Sub-Total, PB1, Jumlah, Tunai) and Malaysian/English (Total, GST, Service Charge)
# conventions, and a label table covering only the first of those makes the arithmetic
# layer silently inert on the other two.
SUBTOTAL_WORDS = ("SUBTOTAL", "SUBTOTA", "JUMLAH")
TOTAL_WORDS = ("TOTAL", "GRANDTOTAL", "TOTA")
TAX_WORDS = ("PB1", "PPN", "PPH", "GST", "SST", "VAT", "TAX", "PAJAK")
SERVICE_WORDS = ("SERVICE", "SERVIS", "SVC", "CHARGE")
IGNORE_WORDS = ("CASH", "TUNAI", "CHANGE", "KEMBALI", "KEMBALIAN", "DEBIT", "CREDIT",
                "TENDER", "PAYMENT", "BAYAR")


def _label(line: str) -> str | None:
    """Classify a receipt line by its leading label, tolerating OCR damage.

    OCR routinely renders SGST as "scsT" and CGST as "cGsT" on thermal print. Matching
    labels exactly would silently drop the tax rows, and every downstream reconciliation
    would then pass by default -- a false negative caused entirely by string handling.
    """
    alpha = "".join(ch for ch in line if ch.isalpha()).upper()
    if not alpha:
        return None

    # Payment/change lines carry large numbers that are not part of the bill arithmetic;
    # mistaking "CASH 200,000" for a total corrupts every downstream check.
    if any(w in alpha for w in IGNORE_WORDS):
        return None
    if any(w in alpha for w in SUBTOTAL_WORDS[1:]) or alpha.startswith("SUBTOTAL"):
        return "subtotal"
    if any(w in alpha for w in SERVICE_WORDS):
        return "service"
    if any(w in alpha for w in TAX_WORDS):
        return "tax"

    head = alpha[:8]
    if head.startswith("SUB"):
        return "subtotal"
    # CGST and SGST differ by a single character, so a plain one-substitution match would
    # classify every SGST row as CGST -- silently collapsing the two tax rows into one and
    # leaving the subtotal+tax=total reconciliation permanently unable to fire. Anchor on
    # the distinguishing first character and allow OCR damage only in the shared "GST".
    if len(head) >= 4 and head[0] == "C" and _edit_le1(head[1:4], "GST"):
        return "cgst"
    if len(head) >= 4 and head[0] == "S" and _edit_le1(head[1:4], "GST"):
        return "sgst"
    if head.startswith("TOTAL") or _edit_le1(head[:5], "TOTAL") or "TOTAL" in alpha:
        return "total"
    if "HSN" in alpha and "@" in line:
        return "item"
    return None


def arithmetic_features(ocr: OcrResult) -> dict[str, float]:
    """Recover the printed numbers and check that the document reconciles.

    Everything here comes from OCR of the image. The ground-truth spec that produced the
    receipt is never consulted -- if OCR misreads a digit, this layer is wrong, exactly as
    it would be in production. Tolerances are relative and generous enough to absorb normal
    OCR error without absorbing a real 1.5% discrepancy.
    """
    f = {
        "arith_parsed": 0.0, "arith_n_items": 0.0,
        "arith_items_vs_subtotal_rel": 0.0, "arith_components_vs_total_rel": 0.0,
        "arith_cgst_sgst_mismatch": 0.0, "arith_implied_gst": 0.0,
        "arith_gst_off_slab": 0.0, "arith_any_break": 0.0,
        "arith_items_reliable": 1.0, "arith_has_service": 0.0,
        "arith_uplift_ratio": 0.0, "arith_total_below_subtotal": 0.0,
        "arith_uplift_implausible": 0.0,
    }
    if not ocr.text:
        return f

    subtotal = cgst = sgst = total = None
    tax = service = None
    items: list[float] = []

    for raw in ocr.text.splitlines():
        line = raw.strip()
        if not line:
            continue
        kind = _label(line)
        v = _num(line)
        if v is None:
            continue
        if kind == "item":
            items.append(v)
        elif kind == "subtotal":
            subtotal = v
        elif kind == "cgst":
            cgst = v
        elif kind == "sgst":
            sgst = v
        elif kind == "tax":
            tax = v
        elif kind == "service":
            service = v
        elif kind == "total":
            # Receipts print several "total"-ish lines; the bill total is the largest.
            total = v if total is None else max(total, v)

    # Indian receipts split GST into CGST+SGST; Indonesian and Malaysian ones print a
    # single tax line (PB1, PPN, GST) plus often a service charge. Normalise to one
    # tax total so the reconciliation works in every convention the corpus contains.
    tax_total = None
    if cgst is not None or sgst is not None:
        tax_total = (cgst or 0.0) + (sgst or 0.0)
    elif tax is not None:
        tax_total = tax

    have = sum(v is not None for v in (subtotal, tax_total, total, service if service is not None else None))
    have = sum(v is not None for v in (subtotal, tax_total, total))
    f["arith_parsed"] = have / 3.0
    f["arith_has_service"] = float(service is not None)
    f["arith_n_items"] = float(len(items))

    # Check 1: do the line items sum to the printed subtotal?
    #
    # This check is the most informative in principle and the most OCR-fragile in practice:
    # it depends on reading EVERY item amount correctly, in small print, and one mangled
    # digit ("4,401.44" read as "44,401.44") destroys the sum. So outliers are rejected
    # against the median before summing, and `arith_items_reliable` records whether the
    # check should be believed at all. The residual stays available to the model as a
    # graded feature; it is deliberately kept out of the human-facing boolean below.
    if items and subtotal and subtotal > 0:
        arr = np.asarray(items, dtype=float)
        med = float(np.median(arr))
        keep = arr[(arr <= med * 4.0) & (arr >= med / 4.0)] if med > 0 else arr
        f["arith_items_reliable"] = float(len(keep) == len(arr))
        if len(keep):
            f["arith_items_vs_subtotal_rel"] = float(min(abs(keep.sum() - subtotal) / subtotal, 5.0))

    # Check 2: does subtotal + tax + service equal the printed total?
    #
    # The service charge is not optional book-keeping. Measured on real CORD receipts,
    # omitting it produced apparent 4-6% "errors" on perfectly genuine restaurant bills,
    # which at a 1% tolerance would have condemned most of them.
    if subtotal and tax_total is not None and total and total > 0:
        expected = subtotal + tax_total + (service or 0.0)
        f["arith_components_vs_total_rel"] = abs(expected - total) / total

    # Check 2b: the ORDER-OF-MAGNITUDE check, which needs no tax line at all.
    #
    # Exact reconciliation only works when every component parses, and on real receipts
    # that is the minority case -- plenty of them have no separate tax line, and OCR drops
    # others. But two things hold on every genuine receipt regardless of layout or
    # language: the total is never LESS than the subtotal (tax and service only add), and
    # the uplift over the subtotal cannot exceed a plausible tax-plus-service ceiling.
    #
    # This turns out to be the strongest available signal against a tampered total,
    # because a transplanted figure lands at an arbitrary magnitude -- a copy-move that
    # replaces 1,591,600 with a 36,000 lifted from a line item leaves a total far below
    # its own subtotal, which no genuine receipt can do.
    if subtotal and total and subtotal > 0 and total > 0:
        uplift = (total - subtotal) / subtotal
        f["arith_uplift_ratio"] = float(np.clip(uplift, -5.0, 20.0))
        f["arith_total_below_subtotal"] = float(uplift < -0.005)
        f["arith_uplift_implausible"] = float(uplift < -0.005 or uplift > MAX_UPLIFT)

    # Check 3: CGST and SGST are equal halves of GST by law. Any gap is an error.
    if cgst is not None and sgst is not None and max(cgst, sgst) > 0:
        f["arith_cgst_sgst_mismatch"] = abs(cgst - sgst) / max(cgst, sgst)

    # Check 4: is the implied tax rate a legal slab (or a blend of slabs)?
    if subtotal and subtotal > 0 and tax_total is not None:
        rate = tax_total / subtotal * 100.0
        f["arith_implied_gst"] = rate
        if 0.0 <= rate <= 28.5:
            # A mixed basket blends slabs, so anything between the lowest and highest slab
            # is plausible. Only distance outside that envelope is evidence.
            f["arith_gst_off_slab"] = 0.0 if rate <= 28.0 else rate - 28.0
        else:
            f["arith_gst_off_slab"] = abs(rate - min(GST_SLABS, key=lambda s: abs(s - rate)))

    # The human-facing verdict fires only on the checks that survive OCR noise: the four
    # headline figures are set in large type and read reliably, whereas the item column does
    # not. A boolean that cried wolf on a third of genuine receipts would be worse than
    # useless in a dispute packet, where every claim has to hold up under challenge.
    f["arith_any_break"] = float(
        f["arith_components_vs_total_rel"] > 0.01
        or f["arith_cgst_sgst_mismatch"] > 0.05
        or f["arith_gst_off_slab"] > 1.5
        or f["arith_uplift_implausible"] > 0
    )
    return f


def provenance_features(path: Path, img: Image.Image) -> dict[str, float]:
    """Was this file ever inside a camera?"""
    f = {"exif_present": 0.0, "exif_has_camera": 0.0, "exif_has_software": 0.0,
         "exif_has_datetime": 0.0, "exif_n_tags": 0.0, "exif_has_iso": 0.0}
    try:
        exif = img.getexif()
    except Exception:
        return f
    if not exif:
        return f
    f["exif_present"] = 1.0
    f["exif_n_tags"] = float(len(exif))
    f["exif_has_camera"] = float(271 in exif or 272 in exif)   # Make / Model
    f["exif_has_software"] = float(305 in exif)                 # Software
    f["exif_has_datetime"] = float(306 in exif or 36867 in exif)
    f["exif_has_iso"] = float(34855 in exif)
    return f


def compression_features(path: Path, img: Image.Image) -> dict[str, float]:
    """JPEG quantisation tables and error-level analysis."""
    f = {"jpeg_q_luma_mean": 0.0, "jpeg_q_dc": 0.0, "jpeg_q_high_freq": 0.0,
         "jpeg_n_tables": 0.0, "ela_mean": 0.0, "ela_std": 0.0, "ela_p99": 0.0,
         "ela_max_block": 0.0, "ela_block_dispersion": 0.0}

    q = getattr(img, "quantization", None) or {}
    f["jpeg_n_tables"] = float(len(q))
    if 0 in q:
        tbl = np.asarray(q[0], dtype=float)
        f["jpeg_q_luma_mean"] = float(tbl.mean())
        f["jpeg_q_dc"] = float(tbl.flat[0])
        f["jpeg_q_high_freq"] = float(tbl.flat[-1])

    # Error-level analysis: recompress at a fixed quality and measure how much each region
    # changes. Regions already at that quality barely move; regions with a different
    # compression history move a lot. A pasted patch shows up as a local hot spot, which is
    # why block dispersion matters more than the global mean.
    try:
        rgb = img.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=90)
        buf.seek(0)
        re_img = Image.open(buf).convert("RGB")
        diff = np.abs(np.asarray(rgb, dtype=np.int16) - np.asarray(re_img, dtype=np.int16))
        ela = diff.max(axis=2).astype(np.float32)
    except Exception:
        return f

    f["ela_mean"] = float(ela.mean())
    f["ela_std"] = float(ela.std())
    f["ela_p99"] = float(np.percentile(ela, 99))

    h, w = ela.shape
    bs = 32
    blocks = [
        ela[y:y + bs, x:x + bs].mean()
        for y in range(0, h - bs, bs) for x in range(0, w - bs, bs)
    ]
    if blocks:
        b = np.asarray(blocks)
        f["ela_max_block"] = float(b.max())
        # A uniformly compressed image has low dispersion across blocks; a spliced one has
        # one block family that behaves differently from the rest.
        f["ela_block_dispersion"] = float(b.std() / (b.mean() + 1e-6))
    return f


def noise_features(img: Image.Image) -> dict[str, float]:
    """Sensor noise is spatially structured; synthesised noise is flat."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    # High-pass residual via a Laplacian kernel, computed with numpy so OpenCV is optional.
    lap = (
        -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    )
    f = {
        "noise_std": float(lap.std()),
        "noise_mad": float(np.median(np.abs(lap - np.median(lap)))),
    }
    h, w = lap.shape
    bs = 48
    blocks = [
        lap[y:y + bs, x:x + bs].std()
        for y in range(0, h - bs, bs) for x in range(0, w - bs, bs)
    ]
    if blocks:
        b = np.asarray(blocks)
        f["noise_block_std_mean"] = float(b.mean())
        # Uniformity: a real photograph's noise varies with local brightness and content.
        f["noise_uniformity"] = float(b.std() / (b.mean() + 1e-6))
    else:
        f["noise_block_std_mean"] = 0.0
        f["noise_uniformity"] = 0.0
    return f


def typography_features(ocr: OcrResult) -> dict[str, float]:
    """Thermal print jitters. Rendered glyphs sit exactly where they were told to."""
    f = {"typo_n_words": 0.0, "typo_conf_mean": 0.0, "typo_conf_std": 0.0,
         "typo_height_cv": 0.0, "typo_baseline_drift": 0.0, "typo_gap_cv": 0.0}
    if len(ocr.words) < 8:
        return f

    conf = np.asarray([w["conf"] for w in ocr.words], dtype=float)
    hts = np.asarray([w["height"] for w in ocr.words], dtype=float)
    f["typo_n_words"] = float(len(ocr.words))
    f["typo_conf_mean"] = float(conf.mean())
    f["typo_conf_std"] = float(conf.std())
    f["typo_height_cv"] = float(hts.std() / (hts.mean() + 1e-6))

    # Baseline drift: within one OCR text line every word should share a top edge. Jittered
    # thermal print and a hand-held photograph both spread it; a flat render does not.
    drifts, gaps = [], []
    by_line: dict[tuple[int, int], list[dict]] = {}
    for w in ocr.words:
        by_line.setdefault((w["block"], w["line"]), []).append(w)
    for ws in by_line.values():
        if len(ws) < 3:
            continue
        tops = np.asarray([w["top"] for w in ws], dtype=float)
        drifts.append(tops.std())
        ws = sorted(ws, key=lambda w: w["left"])
        g = np.asarray([
            ws[i + 1]["left"] - (ws[i]["left"] + ws[i]["width"]) for i in range(len(ws) - 1)
        ], dtype=float)
        if len(g) and g.mean() > 0:
            gaps.append(g.std() / g.mean())

    f["typo_baseline_drift"] = float(np.mean(drifts)) if drifts else 0.0
    f["typo_gap_cv"] = float(np.mean(gaps)) if gaps else 0.0
    return f


def crosscheck_features(
    ocr: OcrResult,
    claimed_amount: float | None,
    claimed_ts: str | None,
    claimed_descriptor: str | None,
) -> dict[str, float]:
    """Compare what the receipt says against what the transaction record says.

    This is the layer that requires owning the ledger, and the only one that can flag a
    genuine, unaltered receipt recycled from a different order. It reads the receipt through
    OCR like every other layer -- it is not given the answer.
    """
    f = {"xc_available": 0.0, "xc_amount_rel_diff": 0.0, "xc_amount_mismatch": 0.0,
         "xc_date_days": 0.0, "xc_date_mismatch": 0.0, "xc_merchant_mismatch": 0.0}
    if not ocr.text:
        return f
    f["xc_available"] = 1.0

    # Amount: find the printed TOTAL.
    total = None
    for raw in ocr.text.splitlines():
        low = raw.lower().strip()
        if low.startswith("total") or " total" in low:
            total = _num(raw) or total
    if total and claimed_amount:
        rel = abs(total - claimed_amount) / max(claimed_amount, 1.0)
        f["xc_amount_rel_diff"] = float(min(rel, 5.0))
        f["xc_amount_mismatch"] = float(rel > 0.02)

    # Date: the printed date against the transaction date.
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", ocr.text)
    if m and claimed_ts:
        try:
            from datetime import datetime
            rd = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            cd = datetime.fromisoformat(str(claimed_ts)[:19])
            days = abs((rd - cd).days)
            f["xc_date_days"] = float(min(days, 999))
            f["xc_date_mismatch"] = float(days > 2)
        except ValueError:
            pass

    # Merchant: does the receipt header correspond to the billing descriptor?
    if claimed_descriptor:
        head = " ".join(ocr.text.strip().splitlines()[:2]).upper()
        desc_tokens = {t for t in re.split(r"[^A-Z]+", str(claimed_descriptor).upper()) if len(t) > 3}
        if desc_tokens:
            f["xc_merchant_mismatch"] = float(not any(t in head for t in desc_tokens))
    return f


FEATURE_ORDER: list[str] = []


def extract(
    path: str | Path,
    claimed_amount: float | None = None,
    claimed_ts: str | None = None,
    claimed_descriptor: str | None = None,
) -> dict[str, float]:
    """Full forensic feature vector for one evidence file."""
    path = Path(path)
    img = Image.open(path)
    img.load()

    ocr = read_ocr(img)
    f: dict[str, float] = {}
    f.update(arithmetic_features(ocr))
    f.update(provenance_features(path, img))
    f.update(compression_features(path, img))
    f.update(noise_features(img))
    f.update(typography_features(ocr))
    f.update(crosscheck_features(ocr, claimed_amount, claimed_ts, claimed_descriptor))
    f["file_kb"] = float(path.stat().st_size / 1024)
    f["img_w"] = float(img.width)
    f["img_h"] = float(img.height)
    return f


# Feature groups, used for the metadata-ablation study. A forger who strips or spoofs EXIF
# removes the PROVENANCE group entirely, so reporting performance without it is the honest
# measure of how much the detector actually understands about the document itself.
GROUPS = {
    "arithmetic": lambda k: k.startswith("arith_"),
    "provenance": lambda k: k.startswith("exif_"),
    "compression": lambda k: k.startswith(("jpeg_", "ela_")),
    "noise": lambda k: k.startswith("noise_"),
    "typography": lambda k: k.startswith("typo_"),
    "crosscheck": lambda k: k.startswith("xc_"),
    "file": lambda k: k in {"file_kb", "img_w", "img_h"},
}


def group_of(name: str) -> str:
    for g, pred in GROUPS.items():
        if pred(name):
            return g
    return "other"
