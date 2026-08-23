"""Visa Compelling Evidence 3.0 qualification gate — rulebook version 2026.04.

This is a RULEBOOK, not a model. It encodes the published CE 3.0 criteria as executable
logic with an effective date, so that a network rule change is a one-file edit and never a
retraining job.

Sources for every constant in this module are recorded in docs/RESEARCH.md section 1.
The critical detail, and the one most implementations get wrong: the four matchable data
elements are NOT interchangeable. They are split into Main and Secondary tiers, and a
qualifying combination always requires at least one Main element as an anchor.

    MAIN       Customer purchase IP
               Customer device fingerprint OR customer device ID   (ONE shared slot)

    SECONDARY  Shipping address
               Customer email address
               Customer account ID

    qualified  <=>  two MAIN elements match across all three transactions
                OR  one MAIN + one SECONDARY match across all three transactions

A naive "any two of four" reading admits `account_id + shipping_address`, which Visa does
not accept -- two Secondary elements with no Main anchor. That false positive tells a
merchant they can win a case they will certainly lose, so the tier logic is the single most
load-bearing piece of code in AEGIS. `NAIVE_RULE_FOR_CONTRAST` below reproduces the wrong
reading deliberately, purely so the console can show the two verdicts side by side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Sequence

RULE_VERSION = "ce3/2026.04"
EFFECTIVE_FROM = date(2026, 4, 18)  # TC40 expansion; see RESEARCH.md section 2

# --- Hard gate constants -----------------------------------------------------------------

ELIGIBLE_REASON_CODES = frozenset({"10.4"})

# Prior transactions must be at least 120 and at most 364 days older than the disputed
# transaction. Visa states the window as 120-365; Stripe's implementation enforces 120-364.
# We take the stricter bound so that AEGIS never over-promises qualification.
PRIOR_MIN_AGE_DAYS = 120
PRIOR_MAX_AGE_DAYS = 364

MIN_PRIOR_TRANSACTIONS = 2

MERCHANDISE_OR_SERVICES = frozenset({"merchandise", "services"})

# --- Element tiers -----------------------------------------------------------------------

# Device fingerprint and device ID collapse into a single logical slot: matching both does
# not earn two Main credits, because Visa treats them as one element expressed two ways.
DEVICE_SLOT = "device_fp_or_id"

MAIN_ELEMENTS: tuple[str, ...] = ("purchase_ip", DEVICE_SLOT)
SECONDARY_ELEMENTS: tuple[str, ...] = ("shipping_address", "customer_email", "customer_account_id")

# Underlying ledger columns that feed the collapsed device slot, in preference order.
DEVICE_SOURCE_FIELDS: tuple[str, ...] = ("device_fingerprint", "device_id")

ELEMENT_LABELS = {
    "purchase_ip": "Customer purchase IP",
    DEVICE_SLOT: "Customer device fingerprint / device ID",
    "shipping_address": "Shipping address",
    "customer_email": "Customer email address",
    "customer_account_id": "Customer account ID",
}

ELEMENT_TIER = {e: "main" for e in MAIN_ELEMENTS} | {e: "secondary" for e in SECONDARY_ELEMENTS}

# Remediation copy is merchant-actionable on purpose: a gap diagnosis that says "missing
# device_fingerprint" is a column name, not advice.
REMEDIATION = {
    "purchase_ip": (
        "Capture and store the customer's IP address at authorisation on every channel. "
        "In-app and server-to-server flows are the usual blind spot -- forward the client IP "
        "rather than the gateway's."
    ),
    DEVICE_SLOT: (
        "Enable device fingerprinting at checkout and persist the fingerprint against the "
        "order. This is the highest-leverage single fix: it is a Main element, so it can "
        "anchor a qualifying pair on its own."
    ),
    "shipping_address": (
        "Persist the normalised shipping address per order. Only applies to physical goods; "
        "services orders must qualify on other elements."
    ),
    "customer_email": (
        "Store the email address used at purchase, normalised (lowercased, plus-tags stripped) "
        "so it matches across orders."
    ),
    "customer_account_id": (
        "Record your internal account identifier on every order, including guest checkouts "
        "that are later linked to an account."
    ),
}


# --- Result types ------------------------------------------------------------------------


@dataclass(frozen=True)
class ElementMatch:
    """Whether one logical element matches across the disputed txn and both priors."""

    element: str
    matched: bool
    tier: str
    value: str | None = None
    missing_on: tuple[str, ...] = ()  # txn ids lacking the field entirely
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", ELEMENT_LABELS.get(self.element, self.element))


@dataclass(frozen=True)
class Gap:
    """One reason a case does not qualify, with the fix."""

    code: str
    detail: str
    remediation: str = ""
    element: str | None = None


@dataclass(frozen=True)
class PriorPair:
    """A candidate pair of prior undisputed transactions plus how well it matches."""

    prior_ids: tuple[str, str]
    matches: tuple[ElementMatch, ...]
    qualified: bool
    main_matched: tuple[str, ...]
    secondary_matched: tuple[str, ...]

    @property
    def score(self) -> tuple[int, int]:
        """Ranking key: Main matches dominate, Secondary breaks ties."""
        return (len(self.main_matched), len(self.secondary_matched))


@dataclass
class QualificationResult:
    qualified: bool
    rule_version: str = RULE_VERSION
    matched_elements: tuple[str, ...] = ()
    best_prior_pair: PriorPair | None = None
    blocking_gaps: list[Gap] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    candidate_prior_count: int = 0
    # Counterfactual: elements that would flip this case to qualified if captured.
    unlock_elements: tuple[str, ...] = ()
    naive_rule_qualified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified,
            "rule_version": self.rule_version,
            "matched_elements": list(self.matched_elements),
            "matched_element_labels": [ELEMENT_LABELS.get(e, e) for e in self.matched_elements],
            "best_prior_pair": (
                {
                    "prior_ids": list(self.best_prior_pair.prior_ids),
                    "main_matched": list(self.best_prior_pair.main_matched),
                    "secondary_matched": list(self.best_prior_pair.secondary_matched),
                    "matches": [
                        {
                            "element": m.element,
                            "label": m.label,
                            "tier": m.tier,
                            "matched": m.matched,
                            "missing_on": list(m.missing_on),
                        }
                        for m in self.best_prior_pair.matches
                    ],
                }
                if self.best_prior_pair
                else None
            ),
            "blocking_gaps": [
                {
                    "code": g.code,
                    "detail": g.detail,
                    "remediation": g.remediation,
                    "element": g.element,
                }
                for g in self.blocking_gaps
            ],
            "remediation": self.remediation,
            "candidate_prior_count": self.candidate_prior_count,
            "unlock_elements": list(self.unlock_elements),
            "unlock_element_labels": [ELEMENT_LABELS.get(e, e) for e in self.unlock_elements],
            "naive_rule_qualified": self.naive_rule_qualified,
            "naive_rule_disagrees": self.naive_rule_qualified and not self.qualified,
        }


# --- Field access helpers ----------------------------------------------------------------


def _get(txn: dict[str, Any], key: str) -> Any:
    v = txn.get(key)
    if v is None:
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    s = str(v).strip()
    return s if s and s.lower() not in {"none", "nan", ""} else None


def _element_value(txn: dict[str, Any], element: str) -> str | None:
    """Resolve a logical element to a comparable value on one transaction."""
    if element == DEVICE_SLOT:
        # The slot matches if EITHER underlying field is present and equal. We return a
        # tagged value so a fingerprint never accidentally compares equal to a device id.
        for fld in DEVICE_SOURCE_FIELDS:
            v = _get(txn, fld)
            if v is not None:
                return f"{fld}:{v}"
        return None
    return _get(txn, element)


def _device_values(txn: dict[str, Any]) -> set[str]:
    """All tagged device values on a transaction (fingerprint and/or id)."""
    out = set()
    for fld in DEVICE_SOURCE_FIELDS:
        v = _get(txn, fld)
        if v is not None:
            out.add(f"{fld}:{v}")
    return out


def _matches_across(txns: Sequence[dict[str, Any]], element: str) -> ElementMatch:
    """Does `element` carry the same value across every transaction supplied?"""
    tier = ELEMENT_TIER[element]

    if element == DEVICE_SLOT:
        # Match if some tagged device value is common to all three transactions. This
        # naturally enforces "fingerprint and id are not two elements" -- they share the slot.
        per_txn = [_device_values(t) for t in txns]
        missing = tuple(str(t.get("txn_id")) for t, vals in zip(txns, per_txn) if not vals)
        if missing:
            return ElementMatch(element, False, tier, None, missing)
        common = set.intersection(*per_txn)
        val = sorted(common)[0] if common else None
        return ElementMatch(element, bool(common), tier, val, ())

    vals = [_element_value(t, element) for t in txns]
    missing = tuple(str(t.get("txn_id")) for t, v in zip(txns, vals) if v is None)
    if missing:
        return ElementMatch(element, False, tier, None, missing)
    allsame = len(set(vals)) == 1
    return ElementMatch(element, allsame, tier, vals[0] if allsame else None, ())


def _qualifies(main_matched: Sequence[str], secondary_matched: Sequence[str]) -> bool:
    """The real Visa tier rule: 2 Main, or 1 Main + 1 Secondary."""
    m, s = len(main_matched), len(secondary_matched)
    return m >= 2 or (m >= 1 and s >= 1)


def NAIVE_RULE_FOR_CONTRAST(main_matched: Sequence[str], secondary_matched: Sequence[str]) -> bool:
    """The common-but-wrong 'any two of four' reading.

    Retained ONLY so the console can display what a naive implementation would have told
    the merchant. Never call this to make a real decision.
    """
    return (len(main_matched) + len(secondary_matched)) >= 2


# --- Date helpers ------------------------------------------------------------------------


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:19]).date()
    except ValueError:
        return None


# --- Prior transaction eligibility -------------------------------------------------------


def eligible_priors(
    disputed: dict[str, Any],
    history: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Gap]]:
    """Filter a customer's history down to CE 3.0-eligible prior transactions.

    Eligibility, per the published criteria: same payment credential, settled/paid, never
    disputed, never reported as fraud (TC40), not a validation charge, carrying a product
    description, and aged 120-364 days before the disputed transaction.
    """
    d_date = _as_date(disputed.get("ts"))
    card = _get(disputed, "card_token")
    gaps: list[Gap] = []
    if d_date is None or card is None:
        gaps.append(
            Gap(
                "MALFORMED_DISPUTED_TXN",
                "Disputed transaction is missing a timestamp or payment credential.",
            )
        )
        return [], gaps

    same_card = [t for t in history if _get(t, "card_token") == card and t.get("txn_id") != disputed.get("txn_id")]
    if not same_card:
        gaps.append(
            Gap(
                "NO_PRIOR_HISTORY",
                "No other transactions exist on this payment credential.",
                "CE 3.0 is unavailable for first-time customers by design. Route these to "
                "standard representment and focus prevention upstream (3DS, delivery proof).",
            )
        )
        return [], gaps

    eligible: list[dict[str, Any]] = []
    n_too_recent = n_too_old = n_disputed = n_tc40 = n_unpaid = n_validation = n_nodesc = 0

    for t in same_card:
        t_date = _as_date(t.get("ts"))
        if t_date is None:
            continue
        age = (d_date - t_date).days
        if age < PRIOR_MIN_AGE_DAYS:
            n_too_recent += 1
            continue
        if age > PRIOR_MAX_AGE_DAYS:
            n_too_old += 1
            continue
        if bool(t.get("disputed")):
            n_disputed += 1
            continue
        if bool(t.get("tc40_reported")):
            n_tc40 += 1
            continue
        if str(t.get("status", "paid")).lower() not in {"paid", "settled", "captured"}:
            n_unpaid += 1
            continue
        if bool(t.get("is_validation_charge")):
            n_validation += 1
            continue
        if _get(t, "product_description") is None:
            n_nodesc += 1
            continue
        eligible.append(t)

    if len(eligible) < MIN_PRIOR_TRANSACTIONS:
        detail = (
            f"Only {len(eligible)} of {len(same_card)} prior transactions on this credential are "
            f"CE 3.0-eligible; {MIN_PRIOR_TRANSACTIONS} are required."
        )
        reasons = []
        if n_too_recent:
            reasons.append(f"{n_too_recent} newer than {PRIOR_MIN_AGE_DAYS} days")
        if n_too_old:
            reasons.append(f"{n_too_old} older than {PRIOR_MAX_AGE_DAYS} days")
        if n_disputed:
            reasons.append(f"{n_disputed} previously disputed")
        if n_tc40:
            reasons.append(f"{n_tc40} previously reported as fraud (TC40)")
        if n_unpaid:
            reasons.append(f"{n_unpaid} not settled")
        if n_validation:
            reasons.append(f"{n_validation} validation charges")
        if n_nodesc:
            reasons.append(f"{n_nodesc} missing a product description")
        if reasons:
            detail += " Excluded: " + ", ".join(reasons) + "."

        rem = ""
        if n_too_recent and not n_nodesc:
            rem = (
                "This case is failing on timing, not data. The 120-day floor means today's "
                "customers become defensible next quarter -- there is nothing to fix here."
            )
        elif n_nodesc:
            rem = (
                "Store a product description on every order. It costs nothing and it is a hard "
                "CE 3.0 requirement on all three transactions."
            )
        gaps.append(Gap("INSUFFICIENT_ELIGIBLE_PRIORS", detail, rem))

    return eligible, gaps


# --- Main entry point --------------------------------------------------------------------


def qualify(
    disputed: dict[str, Any],
    history: Iterable[dict[str, Any]],
    reason_code: str | None = None,
) -> QualificationResult:
    """Evaluate a disputed transaction against the CE 3.0 rulebook.

    `history` is every other transaction known for this customer. Returns a full
    QualificationResult including gap diagnosis and the single-field counterfactual.
    """
    history = list(history)
    rc = str(reason_code or disputed.get("dispute_reason_code") or "").strip()
    result = QualificationResult(qualified=False)

    # Gate 1: reason code.
    if rc not in ELIGIBLE_REASON_CODES:
        result.blocking_gaps.append(
            Gap(
                "INELIGIBLE_REASON_CODE",
                f"Reason code {rc or 'unknown'} is not CE 3.0-eligible. "
                f"Only {', '.join(sorted(ELIGIBLE_REASON_CODES))} (Other Fraud - Card-Absent) qualifies.",
                "Non-10.4 disputes need a different defence: delivery proof for 13.1 "
                "(not received), or product/description evidence for 13.3 (not as described).",
            )
        )
        return result

    # Gate 2: required fields on the disputed transaction itself.
    if _get(disputed, "product_description") is None:
        result.blocking_gaps.append(
            Gap(
                "MISSING_PRODUCT_DESCRIPTION",
                "The disputed transaction has no product description. CE 3.0 requires one on "
                "all three transactions.",
                REMEDIATION.get("product_description", "Store a product description on every order."),
            )
        )
    mos = (_get(disputed, "merchandise_or_services") or "").lower()
    if mos not in MERCHANDISE_OR_SERVICES:
        result.blocking_gaps.append(
            Gap(
                "MISSING_MERCHANDISE_OR_SERVICES",
                "The disputed transaction is not categorised as 'merchandise' or 'services', "
                "which CE 3.0 requires.",
                "Tag every order as merchandise or services at capture time.",
            )
        )

    # Gate 3: eligible prior transactions.
    priors, prior_gaps = eligible_priors(disputed, history)
    result.blocking_gaps.extend(prior_gaps)
    result.candidate_prior_count = len(priors)

    if len(priors) < MIN_PRIOR_TRANSACTIONS:
        result.remediation = _dedupe([g.remediation for g in result.blocking_gaps if g.remediation])
        return result

    # Gate 4: element matching. Evaluate every candidate pair, keep the strongest.
    all_elements = list(MAIN_ELEMENTS) + list(SECONDARY_ELEMENTS)
    pairs: list[PriorPair] = []
    # Newest-first keeps the pair whose data-capture era is closest to the disputed order.
    priors_sorted = sorted(priors, key=lambda t: _as_date(t.get("ts")) or date.min, reverse=True)

    for i in range(len(priors_sorted)):
        for j in range(i + 1, len(priors_sorted)):
            trio = [disputed, priors_sorted[i], priors_sorted[j]]
            matches = tuple(_matches_across(trio, e) for e in all_elements)
            main_m = tuple(m.element for m in matches if m.matched and m.tier == "main")
            sec_m = tuple(m.element for m in matches if m.matched and m.tier == "secondary")
            pairs.append(
                PriorPair(
                    prior_ids=(str(priors_sorted[i]["txn_id"]), str(priors_sorted[j]["txn_id"])),
                    matches=matches,
                    qualified=_qualifies(main_m, sec_m),
                    main_matched=main_m,
                    secondary_matched=sec_m,
                )
            )

    if not pairs:
        result.remediation = _dedupe([g.remediation for g in result.blocking_gaps if g.remediation])
        return result

    best = max(pairs, key=lambda p: (p.qualified, p.score))
    result.best_prior_pair = best
    result.qualified = best.qualified
    result.matched_elements = tuple(best.main_matched) + tuple(best.secondary_matched)
    result.naive_rule_qualified = any(
        NAIVE_RULE_FOR_CONTRAST(p.main_matched, p.secondary_matched) for p in pairs
    )

    if result.qualified:
        result.blocking_gaps = [
            g for g in result.blocking_gaps if g.code == "INSUFFICIENT_ELIGIBLE_PRIORS" and False
        ]
        return result

    # Not qualified on elements -- diagnose precisely why, and what single field would fix it.
    result.blocking_gaps.append(_element_gap(best))
    result.unlock_elements = _unlock_elements(pairs)
    for e in result.unlock_elements:
        result.remediation.append(REMEDIATION[e])
    result.remediation = _dedupe(
        result.remediation + [g.remediation for g in result.blocking_gaps if g.remediation]
    )
    return result


def _element_gap(best: PriorPair) -> Gap:
    m, s = len(best.main_matched), len(best.secondary_matched)
    if m == 0 and s >= 2:
        return Gap(
            "NO_MAIN_ANCHOR",
            f"{s} Secondary elements match ("
            + ", ".join(ELEMENT_LABELS[e] for e in best.secondary_matched)
            + ") but no Main element does. Visa requires at least one Main element -- purchase IP "
            "or device fingerprint/ID -- to anchor any qualifying combination. Two Secondary "
            "elements never qualify on their own.",
            REMEDIATION[DEVICE_SLOT],
            element=DEVICE_SLOT,
        )
    if m == 1 and s == 0:
        return Gap(
            "MAIN_WITHOUT_PARTNER",
            f"One Main element matches ({ELEMENT_LABELS[best.main_matched[0]]}) but nothing pairs "
            "with it. CE 3.0 needs a second Main, or one Secondary alongside it.",
            REMEDIATION["customer_email"],
            element="customer_email",
        )
    if m == 0 and s == 0:
        missing = {mm.element for mm in best.matches if mm.missing_on}
        if missing:
            return Gap(
                "NO_ELEMENTS_CAPTURED",
                "No CE 3.0 data elements are consistently captured across the disputed "
                "transaction and its priors.",
                REMEDIATION[DEVICE_SLOT],
                element=DEVICE_SLOT,
            )
        return Gap(
            "NO_ELEMENTS_MATCH",
            "Every CE 3.0 element is captured but none holds the same value across all three "
            "transactions. This pattern is consistent with genuine account or device change -- "
            "or with a shared credential.",
            "Review whether this customer legitimately changed device and network, or whether "
            "the credential is being used by more than one person.",
        )
    return Gap(
        "ELEMENTS_INSUFFICIENT",
        f"{m} Main and {s} Secondary elements match, which does not satisfy the CE 3.0 tier rule.",
        REMEDIATION[DEVICE_SLOT],
        element=DEVICE_SLOT,
    )


def _unlock_elements(pairs: Sequence[PriorPair]) -> tuple[str, ...]:
    """Counterfactual: which single element, if it matched, would flip the case to qualified?

    This is what makes gap diagnosis actionable. We only count an element as an unlock if it
    is currently NOT matching and adding it satisfies the tier rule for some candidate pair.
    """
    unlocks: list[str] = []
    for element in list(MAIN_ELEMENTS) + list(SECONDARY_ELEMENTS):
        for p in pairs:
            if p.qualified or element in p.main_matched or element in p.secondary_matched:
                continue
            tier = ELEMENT_TIER[element]
            main_m = list(p.main_matched) + ([element] if tier == "main" else [])
            sec_m = list(p.secondary_matched) + ([element] if tier == "secondary" else [])
            if _qualifies(main_m, sec_m):
                unlocks.append(element)
                break
    # Main elements first: they unlock more future cases than Secondary ones do.
    unlocks.sort(key=lambda e: (ELEMENT_TIER[e] != "main", e))
    return tuple(unlocks)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out
