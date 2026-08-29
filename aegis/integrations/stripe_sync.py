"""Stripe integration — pull live disputes and check AEGIS against Stripe's own CE 3.0 verdict.

WHY STRIPE IS THE RIGHT INTEGRATION AND NOT DECORATION.

Stripe implements Compelling Evidence 3.0 natively. A dispute object carries
`evidence_details.enhanced_eligibility.visa_compelling_evidence_3`, whose `status` is one of
`qualified`, `requires_action` or `not_qualified`, plus a `required_actions` array naming
what is missing. That makes Stripe an independent oracle for exactly the determination this
project exists to make.

So the integration is not "we can read your disputes". It is:

    Stripe says      requires_action  ["missing_merchandise_or_services"]
    AEGIS says       NOT QUALIFIED - no Main anchor; device fingerprint absent on order #2
    Agreement        both reject, for DIFFERENT reasons

Stripe tells a merchant *that* a submission is incomplete. AEGIS tells them *why the case
was already lost 120 days ago* and which single field would have changed it. Running both
against the same dispute is the clearest possible demonstration that the rulebook in
`rules/ce3` is a faithful implementation rather than an approximation -- and any disagreement
is a bug in one of them, which is a test worth having.

CREDENTIALS. Reads `STRIPE_SECRET_KEY` from the environment. Never hard-code a key and never
commit one. Without a key this module reports `configured: False` and the console shows the
setup path instead of pretending to have data. Use a **restricted key with read-only access
to Disputes and Charges** -- nothing here writes to Stripe, and it must stay that way:
AEGIS assembles evidence for a human to submit, it never submits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Stripe's own CE 3.0 verdicts.
STRIPE_STATUSES = ("qualified", "requires_action", "not_qualified")

# Stripe's required_actions codes, in plain language.
ACTION_LABELS = {
    "missing_merchandise_or_services": "Transaction not categorised as merchandise or services",
    "missing_disputed_transaction_description": "Disputed transaction has no product description",
    "missing_prior_undisputed_transaction_description": "A prior transaction has no product description",
    "missing_prior_undisputed_transactions": "Fewer than two qualifying prior transactions",
    "missing_customer_identifiers": "Not enough matching customer data elements",
}


@dataclass
class StripeDispute:
    dispute_id: str
    charge_id: str | None
    amount: float
    currency: str
    reason: str
    network_reason_code: str | None
    status: str
    created: str
    ce3_status: str | None = None
    ce3_required_actions: list[str] = field(default_factory=list)
    prior_charges: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dispute_id": self.dispute_id,
            "charge_id": self.charge_id,
            "amount": self.amount,
            "currency": self.currency,
            "reason": self.reason,
            "network_reason_code": self.network_reason_code,
            "status": self.status,
            "created": self.created,
            "ce3_status": self.ce3_status,
            "ce3_required_actions": self.ce3_required_actions,
            "ce3_required_action_labels": [
                ACTION_LABELS.get(a, a) for a in self.ce3_required_actions
            ],
            "ce3_eligible_reason_code": self.network_reason_code == "10.4",
        }


def is_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _client():
    import stripe

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def status() -> dict[str, Any]:
    """Whether the integration is live, without leaking any part of the key."""
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    mode = "test" if key.startswith(("sk_test_", "rk_test_")) else (
        "live" if key.startswith(("sk_live_", "rk_live_")) else None
    )
    return {
        "configured": bool(key),
        "mode": mode,
        # Live keys are flagged rather than blocked: reading disputes is harmless, but the
        # operator should know which account they are looking at.
        "warning": (
            "A LIVE key is configured. AEGIS only reads from Stripe and never submits "
            "evidence, but prefer a restricted read-only test key for demos."
            if mode == "live" else None
        ),
        "scopes_needed": ["disputes:read", "charges:read"],
        "writes_to_stripe": False,
    }


def fetch_disputes(limit: int = 25) -> list[StripeDispute]:
    """Pull recent disputes with their CE 3.0 eligibility as Stripe assesses it."""
    stripe = _client()
    out: list[StripeDispute] = []
    resp = stripe.Dispute.list(limit=min(limit, 100), expand=["data.charge"])

    for d in resp.auto_paging_iter():
        if len(out) >= limit:
            break
        details = d.get("evidence_details") or {}
        enh = (details.get("enhanced_eligibility") or {}).get(
            "visa_compelling_evidence_3"
        ) or {}
        charge = d.get("charge")
        charge_id = charge if isinstance(charge, str) else (charge or {}).get("id")
        pmd = (charge or {}).get("payment_method_details", {}) if isinstance(charge, dict) else {}
        card = pmd.get("card", {}) if isinstance(pmd, dict) else {}

        out.append(StripeDispute(
            dispute_id=d["id"],
            charge_id=charge_id,
            amount=(d.get("amount") or 0) / 100.0,
            currency=(d.get("currency") or "usd").upper(),
            reason=d.get("reason") or "unknown",
            network_reason_code=card.get("network_reason_code"),
            status=d.get("status") or "unknown",
            created=datetime.fromtimestamp(
                d.get("created", 0), tz=timezone.utc
            ).isoformat(),
            ce3_status=enh.get("status"),
            ce3_required_actions=list(enh.get("required_actions") or []),
        ))
    return out


def charge_history(customer_id: str | None, charge_id: str | None, limit: int = 40) -> list[dict]:
    """Prior charges for the same customer, mapped into the shape the rulebook reads.

    Stripe exposes the CE 3.0 elements under different names than the rulebook uses, so the
    mapping is explicit here rather than buried: `customer_purchase_ip` is the Main IP
    element, `customer_email_address` and shipping address are Secondary. Stripe does not
    expose a device fingerprint on the charge object, so on Stripe data the device Main
    element is unavailable -- the mirror image of IEEE-CIS, where IP was the missing one.
    """
    stripe = _client()
    if not customer_id:
        return []
    charges = stripe.Charge.list(customer=customer_id, limit=min(limit, 100))
    rows = []
    for c in charges.auto_paging_iter():
        if len(rows) >= limit:
            break
        billing = (c.get("billing_details") or {})
        shipping = (c.get("shipping") or {})
        addr = shipping.get("address") or billing.get("address") or {}
        rows.append({
            "txn_id": c["id"],
            "card_token": (c.get("payment_method") or c.get("source", {}) or {}).get("id")
            if isinstance(c.get("source"), dict) else c.get("payment_method"),
            "ts": datetime.fromtimestamp(c.get("created", 0), tz=timezone.utc),
            "status": "paid" if c.get("paid") else "unpaid",
            "disputed": bool(c.get("disputed")),
            "tc40_reported": False,
            "is_validation_charge": (c.get("amount") or 0) == 0,
            "product_description": c.get("description"),
            "merchandise_or_services": None,
            # Main elements
            "purchase_ip": c.get("ip") or (c.get("metadata") or {}).get("customer_purchase_ip"),
            "device_fingerprint": (c.get("metadata") or {}).get("device_fingerprint"),
            "device_id": (c.get("metadata") or {}).get("device_id"),
            # Secondary elements
            "customer_email": billing.get("email") or c.get("receipt_email"),
            "customer_account_id": c.get("customer"),
            "shipping_address": "|".join(
                str(addr.get(k) or "") for k in ("line1", "postal_code", "country")
            ) or None,
            "amount": (c.get("amount") or 0) / 100.0,
        })
    return rows


def compare(aegis_result: dict[str, Any], stripe_dispute: StripeDispute) -> dict[str, Any]:
    """Set AEGIS's verdict against Stripe's for the same dispute.

    Disagreement is the interesting case and is reported as such rather than smoothed over:
    if Stripe qualifies a dispute the rulebook rejects, one of the two is wrong and it is
    worth knowing which before a representment is filed on the strength of it.
    """
    aegis_q = bool(aegis_result.get("qualified"))
    s = stripe_dispute.ce3_status
    stripe_q = s == "qualified"

    if s is None:
        agreement = "stripe_not_assessed"
        note = (
            "Stripe has not assessed this dispute for CE 3.0 -- it is either not reason code "
            "10.4 or the enhanced-eligibility object is absent."
        )
    elif aegis_q == stripe_q:
        agreement = "agree"
        note = (
            "Both qualify the dispute." if aegis_q else
            "Both reject the dispute. Stripe reports what is missing from the SUBMISSION; "
            "AEGIS reports which data element was never captured, and when."
        )
    else:
        agreement = "disagree"
        note = (
            f"AEGIS says {'qualified' if aegis_q else 'not qualified'} while Stripe says "
            f"'{s}'. One of the two is wrong; resolve before filing. The most common cause "
            f"is prior transactions Stripe can see that are absent from the ledger AEGIS was "
            f"given, or vice versa."
        )

    return {
        "agreement": agreement,
        "aegis_qualified": aegis_q,
        "stripe_ce3_status": s,
        "stripe_required_actions": [
            ACTION_LABELS.get(a, a) for a in stripe_dispute.ce3_required_actions
        ],
        "aegis_blocking_gaps": [g.get("code") for g in aegis_result.get("blocking_gaps", [])],
        "aegis_unlock_elements": aegis_result.get("unlock_element_labels", []),
        "note": note,
        "what_aegis_adds": (
            "Stripe evaluates the evidence you are about to submit. AEGIS evaluates whether "
            "the case was winnable at all, names the field that decided it, and -- because "
            "CE 3.0 needs priors aged 120-364 days -- tells you what to start capturing now "
            "so that comparable disputes qualify next quarter."
        ),
    }
