"""Genuine-Intent fusion: is this really fraud, or a confused customer?

Merchants treat every dispute as one thing. It is three, and they need opposite responses:

  criminal_fraud           Someone else used the card. The cardholder is a victim. Fighting
                           this is both wrong and unwinnable, and pursuing it burns money.
  first_party_misuse       The cardholder made the purchase and disputed it anyway. This is
                           the class CE 3.0 exists to defeat, and firm representment is right.
  genuine_service_failure  The goods never arrived, or were not as described. The customer
                           has a real grievance. Fighting it wins a chargeback and loses a
                           customer; a fast refund is cheaper than both.

Predicting the class is what lets AEGIS choose the TONE of the response, not just whether to
fight. That is the difference between a chargeback tool and a customer-relationship one.

The label used in training is the latent intent recorded by the generative process, which is
the one place it is legitimate to consult it: here it is the TARGET, not a feature. Nothing
in `FEATURES` below touches it, and `synth/ledger.py` keeps the issuer effect hidden from
this model exactly as it does from Side A.
"""

from __future__ import annotations

from dataclasses import dataclass

INTENTS = ("criminal_fraud", "first_party_misuse", "genuine_service_failure")

# Recommended actions, ordered from most conciliatory to most adversarial.
ACTIONS = (
    "ACCEPT_LOSS",
    "SOFT_REFUND",
    "REPRESENT_STANDARD",
    "REPRESENT_CE3",
    "ESCALATE_FORENSIC",
)

FEATURES = [
    "qualified", "n_matched", "n_main_matched", "candidate_prior_count",
    "log_amount", "descriptor_is_clear", "delivery_confirmed", "avs_match", "cvv_match",
    "threeds_authenticated", "post_purchase_usage_days", "days_to_dispute",
    "customer_txn_count", "customer_tenure_days", "customer_prior_disputes",
    "customer_dispute_rate", "amount_vs_customer_mean", "is_subscription",
    "has_customer_evidence", "tc40_reported",
    # Side B's verdict, fed into Side A's world. This is the actual fusion: a document that
    # fails forensic examination is strong evidence AGAINST criminal fraud, because a
    # cardholder whose card was genuinely stolen has no reason to fabricate a receipt for a
    # purchase that was never theirs. Without this feature the intent model scored 67%
    # criminal_fraud on a case whose receipt had a visibly doctored total.
    "evidence_tamper_score",
]
CATEGORICAL = ["category", "merchandise_or_services", "channel", "reason_code"]


@dataclass
class Recommendation:
    action: str
    tone: str
    rationale: str
    confidence: float


def recommend(
    intent_probs: dict[str, float],
    qualified: bool,
    win_prob: float,
    evidence_verdict: str | None,
    amount_inr: float,
    break_even: float,
) -> Recommendation:
    """Turn the intent distribution plus both sides' signals into one recommended action.

    The ordering of these checks is the product's opinion, and it is deliberate.

    Tampered evidence is checked FIRST, before economics. A customer who submits fabricated
    proof has changed what the case is about: it is no longer a disagreement over a purchase
    but a documented attempt to obtain money by deception, and it is worth contesting even
    when the arithmetic says the amount is too small to chase.

    Genuine service failure is checked SECOND, before any decision to fight. Winning a
    dispute against a customer whose parcel genuinely never arrived is a loss disguised as a
    win -- the chargeback is recovered and the customer never returns. Economics only get a
    vote once those two questions are settled.
    """
    top = max(intent_probs, key=intent_probs.get)
    conf = float(intent_probs[top])

    if evidence_verdict == "TAMPERED":
        return Recommendation(
            "ESCALATE_FORENSIC",
            "firm, evidence-led",
            "Customer-submitted evidence failed a deterministic integrity check. Contest "
            "with the forensic report attached; the document itself is now the strongest "
            "part of the case, independent of the transaction history.",
            conf,
        )

    if top == "genuine_service_failure" and conf > 0.45:
        return Recommendation(
            "SOFT_REFUND",
            "apologetic, retain the customer",
            "The signals point to a fulfilment or description failure rather than misuse. "
            "Refunding directly costs less than a contested chargeback and keeps a customer "
            "who has not done anything wrong.",
            conf,
        )

    if top == "criminal_fraud" and conf > 0.50:
        return Recommendation(
            "ACCEPT_LOSS",
            "neutral, no customer contact",
            "The pattern is consistent with third-party fraud, where the cardholder is the "
            "victim. Representment is unlikely to succeed and pursuing the cardholder is "
            "inappropriate. Concede and address the authorisation gap upstream.",
            conf,
        )

    if qualified and win_prob >= break_even:
        return Recommendation(
            "REPRESENT_CE3",
            "firm, rule-led",
            "The case satisfies the Compelling Evidence 3.0 criteria and the expected "
            "recovery exceeds the cost of contesting it. Submit the CE 3.0 bundle.",
            conf,
        )

    if qualified:
        return Recommendation(
            "ACCEPT_LOSS",
            "neutral",
            "The case qualifies for CE 3.0, but at this disputed value the expected recovery "
            "does not cover the cost of contesting it. Qualification is not a reason to "
            "fight a case that loses money either way.",
            conf,
        )

    if win_prob >= break_even:
        return Recommendation(
            "REPRESENT_STANDARD",
            "firm",
            "The case does not qualify for CE 3.0, but the standard evidence available is "
            "strong enough that the expected recovery still exceeds the cost of contesting.",
            conf,
        )

    return Recommendation(
        "ACCEPT_LOSS",
        "neutral",
        "Neither the CE 3.0 criteria nor the expected recovery support contesting this "
        "case. Conceding is the cheaper outcome; the gap diagnosis on Side A shows what to "
        "capture so that similar cases qualify in future.",
        conf,
    )
