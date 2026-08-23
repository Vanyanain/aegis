"""Deterministic evidence rules — the Side B counterpart to the CE 3.0 rulebook.

WHY A RULE LAYER AT ALL, WHEN THERE IS A MODEL.

Leave-one-family-out evaluation exposed a structural weakness that no amount of retraining
fixes. When the `recycled` family was held out, the detector scored 0.000 recall on it: with
every other fake family separable on provenance and compression, the model never needed to
learn the ledger cross-check, so it did not -- and a genuine receipt from a different order
sailed straight through. A learned model only uses a signal it was rewarded for using.

But "this receipt says Rs 8,140 and the charge was Rs 2,199" is not a probabilistic
judgement. It is arithmetic. Facts like that belong in a rulebook where they fire every
time, on evidence the model has never seen, with a sentence a human can check -- exactly as
the CE 3.0 gate does on Side A.

So the rules below run INDEPENDENTLY of the model and can raise the verdict on their own.
The model contributes graded suspicion; the rules contribute certainty. Reporting both
separately is also what keeps the metrics honest: docs/METRICS.md shows model-only,
rules-only, and combined, so nobody has to guess which layer is carrying the result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

RULE_VERSION = "evidence/2026.08"

# A receipt total may differ from the settled amount by rounding, tips or partial capture.
# Beyond this it is a different number, not a rounding artefact.
AMOUNT_TOLERANCE = 0.02

# Receipts are dated the day of purchase. A settlement can lag by a day or two.
DATE_TOLERANCE_DAYS = 3

# Relative tolerance on the internal reconciliation. Set above plausible OCR error so a
# misread digit does not become a tamper accusation.
ARITHMETIC_TOLERANCE = 0.01

# Highest legal Indian GST slab. A blended basket cannot imply more than this.
MAX_GST_RATE = 28.0

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class TamperFlag:
    code: str
    severity: str
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _fmt(v: float) -> str:
    return f"Rs {v:,.2f}"


def evaluate(
    features: dict[str, float],
    claimed_amount: float | None = None,
    claimed_ts: str | None = None,
    descriptor_is_clear: bool = True,
) -> list[TamperFlag]:
    """Apply the deterministic evidence rules to one extracted feature vector.

    Every flag carries the numbers it fired on, so the narrative in a dispute packet can
    quote them rather than assert a conclusion.
    """
    flags: list[TamperFlag] = []
    g = features.get

    # --- Ledger cross-check. The model cannot be relied on to learn this (see module
    # docstring), and it is the only thing that catches an authentic receipt from a
    # different order.
    if g("xc_available", 0.0) and claimed_amount:
        rel = float(g("xc_amount_rel_diff", 0.0))
        if rel > AMOUNT_TOLERANCE:
            implied = claimed_amount * (1 + rel)
            flags.append(TamperFlag(
                "LEDGER_AMOUNT_MISMATCH",
                "critical" if rel > 0.15 else "high",
                f"The receipt total disagrees with the settled transaction by {rel:.1%}. "
                f"The charge was {_fmt(claimed_amount)}; the document reads approximately "
                f"{_fmt(implied)}. A receipt supporting this dispute must be for this order.",
                {"claimed_amount_inr": claimed_amount, "relative_difference": rel},
            ))

    amount_mismatched = bool(
        g("xc_available", 0.0) and claimed_amount
        and float(g("xc_amount_rel_diff", 0.0)) > AMOUNT_TOLERANCE
    )

    if g("xc_date_mismatch", 0.0):
        days = float(g("xc_date_days", 0.0))
        if days > DATE_TOLERANCE_DAYS:
            # A date on its own is the field OCR most often mangles, so a lone date gap is
            # only worth a review. A document that disagrees on date AND amount is not a
            # misread -- it is a different order, and that is decisive.
            flags.append(TamperFlag(
                "LEDGER_DATE_MISMATCH",
                "critical" if (days > 60 and amount_mismatched) else "medium",
                f"The date printed on the receipt is {days:.0f} days from the transaction "
                f"date. Purchase receipts are dated at the point of sale; a gap this large "
                f"indicates the document belongs to a different order.",
                {"days_apart": days},
            ))

    # Only meaningful when the billing descriptor is recognisable in the first place. An
    # opaque descriptor such as "SVCS*BLR 4471" cannot be matched against any store name,
    # so firing on it would penalise the merchant for their own descriptor hygiene -- which
    # is a problem AEGIS reports on Side A, not a reason to doubt the customer's receipt.
    if g("xc_merchant_mismatch", 0.0) and descriptor_is_clear:
        flags.append(TamperFlag(
            "LEDGER_MERCHANT_MISMATCH",
            "high" if amount_mismatched else "medium",
            "The merchant name on the receipt does not correspond to the billing descriptor "
            "on the disputed transaction.",
            {"descriptor_is_clear": True},
        ))

    # --- Internal arithmetic. Independent of the ledger: a document that does not add up
    # is defective on its own terms.
    comp = float(g("arith_components_vs_total_rel", 0.0))
    if comp > ARITHMETIC_TOLERANCE:
        flags.append(TamperFlag(
            "ARITHMETIC_INCONSISTENT",
            "high" if comp > 0.05 else "medium",
            f"Subtotal plus CGST plus SGST does not equal the printed total; they differ by "
            f"{comp:.1%}. Image generators treat numerals as visual tokens rather than "
            f"quantities, so a fabricated receipt commonly looks correct while failing to "
            f"reconcile.",
            {"relative_difference": comp},
        ))

    cs = float(g("arith_cgst_sgst_mismatch", 0.0))
    if cs > 0.05:
        flags.append(TamperFlag(
            "TAX_SPLIT_INVALID",
            "high",
            f"CGST and SGST differ by {cs:.1%}. For an intra-state supply they are equal "
            f"halves of the GST charged, so unequal values indicate the figures were not "
            f"computed from the goods.",
            {"relative_difference": cs},
        ))

    rate = float(g("arith_implied_gst", 0.0))
    off = float(g("arith_gst_off_slab", 0.0))
    if off > 1.5 and rate > 0:
        flags.append(TamperFlag(
            "GST_RATE_IMPLAUSIBLE",
            "medium",
            f"The tax charged implies a GST rate of {rate:.1f}%, which is outside the legal "
            f"slabs (0/5/12/18/28%) and outside any blend of them.",
            {"implied_rate_pct": rate, "distance_from_nearest_slab": off},
        ))

    # Item amounts are set in the smallest type on the page and are the least reliable
    # thing OCR reads, so this threshold sits well above plausible recognition error.
    items = float(g("arith_items_vs_subtotal_rel", 0.0))
    if items > 0.05 and g("arith_items_reliable", 0.0):
        flags.append(TamperFlag(
            "LINE_ITEMS_DO_NOT_SUM",
            "medium",
            f"The line items sum to a figure {items:.1%} away from the printed subtotal.",
            {"relative_difference": items},
        ))

    return flags


# Flags derived from OCR are only as good as the recognition behind them. A misread digit
# in the tax line produces a real arithmetic discrepancy from a perfectly genuine receipt,
# so these cannot condemn a document on their own.
OCR_DERIVED = frozenset({
    "ARITHMETIC_INCONSISTENT", "TAX_SPLIT_INVALID",
    "GST_RATE_IMPLAUSIBLE", "LINE_ITEMS_DO_NOT_SUM",
})

# How much independent model suspicion is needed before an OCR-derived flag may condemn.
# Set well below the model's own 0.5 decision point: the bar is corroboration, not proof.
CORROBORATION_THRESHOLD = 0.25


def verdict(
    model_score: float,
    flags: list[TamperFlag],
    threshold: float = 0.5,
) -> dict:
    """Combine the graded model score with the deterministic flags.

    Accusing a customer of submitting forged evidence is the most damaging thing this system
    can say, and it is said about a real person on the strength of a scanned photograph. So
    the bar for TAMPERED is set by how ROBUST the triggering evidence is, not merely how
    severe it sounds:

    * A `critical` flag condemns on its own. Those come from the ledger cross-check -- the
      receipt total against the settled amount -- which is arithmetic on two numbers we hold
      directly and does not depend on reading small print correctly.

    * A `high` flag derived from OCR needs the model to agree. A single misrecognised digit
      in a tax line yields a genuine-looking arithmetic break on a perfectly authentic
      receipt, and that failure mode is common enough to be seen in the first case pulled
      off the queue. Uncorroborated, such a flag drops the verdict to REVIEW: still surfaced
      to an analyst, but not an accusation.

    The model can raise a case to SUSPECT on its own, and it can never clear one the ledger
    rules have condemned: a document that contradicts the transaction record is not
    authentic merely because it photographs well.
    """
    worst = max((SEVERITY_ORDER[f.severity] for f in flags), default=-1)
    has_critical = any(f.severity == "critical" for f in flags)
    robust_high = any(
        f.severity == "high" and f.code not in OCR_DERIVED for f in flags
    )
    ocr_high = any(
        f.severity == "high" and f.code in OCR_DERIVED for f in flags
    )
    corroborated = model_score >= CORROBORATION_THRESHOLD

    rule_condemns = has_critical or robust_high or (ocr_high and corroborated)
    model_says_tampered = model_score >= threshold

    if rule_condemns:
        label, driver = "TAMPERED", "rule"
    elif model_says_tampered:
        label, driver = "SUSPECT", "model"
    elif worst >= SEVERITY_ORDER["medium"] or ocr_high:
        label, driver = "REVIEW", "rule"
    else:
        label, driver = "VERIFIED", "model"

    return {
        "label": label,
        "driver": driver,
        "authenticity_score": float(1.0 - model_score),
        "tamper_score": float(model_score),
        "flags": [f.as_dict() for f in flags],
        "highest_severity": (
            [k for k, v in SEVERITY_ORDER.items() if v == worst][0] if worst >= 0 else "none"
        ),
        "corroborated": bool(corroborated),
        "rule_version": RULE_VERSION,
        "explanation": _narrate(label, driver, flags, model_score),
    }


def _narrate(label: str, driver: str, flags: list[TamperFlag], score: float) -> str:
    if not flags and label == "VERIFIED":
        return (
            f"No deterministic rule was triggered and the forensic model puts the "
            f"probability of tampering at {score:.1%}. The document reconciles internally "
            f"and agrees with the transaction record."
        )
    if driver == "rule":
        top = sorted(flags, key=lambda f: -SEVERITY_ORDER[f.severity])[0]
        if label == "REVIEW":
            return (
                f"Flagged for analyst review, not condemned: {top.detail} This finding is "
                f"derived from optical character recognition and is not independently "
                f"corroborated -- the forensic model scores tampering probability at only "
                f"{score:.1%}, so a recognition error is at least as likely as tampering."
            )
        return (
            f"{label} on a deterministic rule: {top.detail} "
            f"The forensic model independently scores tampering probability at {score:.1%}."
        )
    return (
        f"{label} on the forensic model, which scores tampering probability at {score:.1%}. "
        f"{len(flags)} supporting rule observation(s) recorded."
    )
