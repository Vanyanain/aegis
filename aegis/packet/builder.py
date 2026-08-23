"""Evidence Packet Builder (M5).

Assembles everything needed to contest one dispute into a single reviewable bundle: the
CE 3.0 element grid across the three transactions, a reason-code-mapped rebuttal narrative,
the forensic report on any customer-submitted evidence, and the hashes that tie it together.

THE PACKET IS NEVER AUTO-SUBMITTED. It is built for a human to read, check and export. A
model's confidence is not a mandate to file a legal-adjacent document with a card network on
a merchant's behalf, and `ready_to_submit` is a statement about completeness, never an
instruction to send.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aegis.evidence_log.chain import sha256_str
from rules.ce3 import v2026_04 as ce3

# What actually persuades an issuer differs by reason code. Sending CE 3.0 prior-transaction
# evidence against a "goods not received" claim answers a question nobody asked.
REASON_CODE_STRATEGY = {
    "10.4": {
        "title": "Other Fraud - Card-Absent Environment",
        "what_the_issuer_asks": (
            "The cardholder states they did not authorise this transaction."
        ),
        "winning_argument": (
            "Establish that this cardholder has an established, undisputed relationship with "
            "the merchant on this same credential, evidenced by matching device, network and "
            "identity data across prior orders."
        ),
        "ce3_eligible": True,
    },
    "13.1": {
        "title": "Merchandise / Services Not Received",
        "what_the_issuer_asks": "The cardholder states the goods or services never arrived.",
        "winning_argument": (
            "Produce delivery confirmation, tracking to the cardholder's address, or evidence "
            "of service access and consumption after the purchase date."
        ),
        "ce3_eligible": False,
    },
    "13.3": {
        "title": "Not as Described or Defective Merchandise",
        "what_the_issuer_asks": (
            "The cardholder states the item materially differed from its description."
        ),
        "winning_argument": (
            "Produce the product description as displayed at purchase, item specifications, "
            "and the returns policy the cardholder accepted."
        ),
        "ce3_eligible": False,
    },
}


def build(case: dict[str, Any], log) -> dict[str, Any]:
    """Assemble the packet for one scored case."""
    rc = str(case["reason_code"])
    strategy = REASON_CODE_STRATEGY.get(rc, {
        "title": f"Reason code {rc}", "what_the_issuer_asks": "Unmapped reason code.",
        "winning_argument": "Manual review required.", "ce3_eligible": False,
    })
    q = case["qualification"]

    sections: list[dict[str, Any]] = []
    blockers: list[str] = []

    # --- Section 1: CE 3.0 element grid ------------------------------------------
    if strategy["ce3_eligible"]:
        if q["qualified"]:
            sections.append({
                "id": "ce3_elements",
                "title": "Compelling Evidence 3.0 - matched data elements",
                "status": "complete",
                "rule_version": q["rule_version"],
                "matched_elements": q["matched_element_labels"],
                "disputed_transaction": q.get("disputed_transaction"),
                "prior_transactions": q.get("prior_transactions", []),
                "statement": _ce3_statement(q),
            })
        else:
            blockers.append(
                "Does not satisfy CE 3.0: "
                + (q["blocking_gaps"][0]["detail"] if q["blocking_gaps"] else "unknown gap")
            )
            sections.append({
                "id": "ce3_elements",
                "title": "Compelling Evidence 3.0 - not available",
                "status": "blocked",
                "rule_version": q["rule_version"],
                "gaps": q["blocking_gaps"],
                "remediation": q["remediation"],
                "statement": (
                    "This dispute cannot be contested under CE 3.0. Contest on standard "
                    "evidence, or concede. The remediation below governs whether comparable "
                    "disputes qualify from roughly 120 days after the capture change."
                ),
            })

    # --- Section 2: forensic report on customer-submitted evidence ----------------
    ev = case.get("evidence")
    if ev:
        sections.append({
            "id": "evidence_forensics",
            "title": "Forensic examination of customer-submitted evidence",
            "status": "complete",
            "verdict": ev["label"],
            "authenticity_score": ev["authenticity_score"],
            "driver": ev["driver"],
            "rule_version": ev["rule_version"],
            "findings": ev["flags"],
            "measurements": ev.get("top_features", []),
            "statement": ev["explanation"],
        })

    # --- Section 3: rebuttal narrative --------------------------------------------
    sections.append({
        "id": "narrative",
        "title": f"Rebuttal narrative - {rc} {strategy['title']}",
        "status": "complete",
        "issuer_question": strategy["what_the_issuer_asks"],
        "argument": strategy["winning_argument"],
        "statement": _narrative(case, q, ev, strategy),
    })

    # --- Section 4: transaction and descriptor record ------------------------------
    sections.append({
        "id": "transaction_record",
        "title": "Transaction record",
        "status": "complete",
        "transaction": q.get("disputed_transaction"),
        "descriptor_is_clear": case.get("descriptor_is_clear"),
        "statement": (
            "The billing descriptor presented to the cardholder is shown above. An "
            "unrecognisable descriptor is a leading cause of first-party misuse and is worth "
            "fixing regardless of the outcome of this dispute."
            if not case.get("descriptor_is_clear")
            else "The billing descriptor clearly identifies the merchant."
        ),
    })

    bundle = {
        "dispute_id": case["dispute_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reason_code": rc,
        "amount_inr": case["amount_inr"],
        "recommendation": case["recommendation"],
        "win_probability": case["win_prob"],
        "break_even_probability": case["break_even"],
        "sections": sections,
        "blockers": blockers,
        # "Ready" means the bundle is complete and internally consistent -- NOT that it
        # should be sent. A human decides that.
        "ready_to_submit": bool(not blockers and case["recommendation"]["action"].startswith(
            ("REPRESENT", "ESCALATE")
        )),
        "auto_submit": False,
        "submission_note": (
            "AEGIS does not submit representments. Export this bundle, review every section, "
            "and file it through your acquirer or processor."
        ),
    }
    bundle["bundle_sha256"] = sha256_str(
        f"{bundle['dispute_id']}|{bundle['generated_at']}|{len(sections)}|{q['rule_version']}"
    )
    bundle["evidence_log_head"] = log.head
    return bundle


def _ce3_statement(q: dict[str, Any]) -> str:
    els = ", ".join(q["matched_element_labels"])
    priors = q.get("prior_transactions", [])
    ids = " and ".join(p["txn_id"] for p in priors) if priors else "the prior transactions"
    return (
        f"The disputed transaction and two prior undisputed transactions on the same payment "
        f"credential ({ids}) share the following data elements: {els}. Both prior "
        f"transactions fall within the required {ce3.PRIOR_MIN_AGE_DAYS}-"
        f"{ce3.PRIOR_MAX_AGE_DAYS} day window, were settled, were never disputed, and were "
        f"never reported as fraud. This satisfies the Compelling Evidence 3.0 criteria under "
        f"rulebook {q['rule_version']}."
    )


def _narrative(case: dict[str, Any], q: dict[str, Any], ev: dict | None,
               strategy: dict[str, Any]) -> str:
    parts = [
        f"The cardholder disputes a charge of Rs {case['amount_inr']:,.2f} under reason code "
        f"{case['reason_code']} ({strategy['title']}). {strategy['what_the_issuer_asks']}"
    ]
    if q["qualified"]:
        parts.append(
            "The transaction history establishes an ongoing, undisputed relationship between "
            "this cardholder and this merchant on the same payment credential, matching on "
            + ", ".join(q["matched_element_labels"]) + "."
        )
    if ev and ev["label"] in ("TAMPERED", "SUSPECT"):
        codes = ", ".join(f["code"] for f in ev["flags"]) or "forensic model assessment"
        parts.append(
            f"The evidence submitted in support of the dispute did not pass forensic "
            f"examination ({codes}). The examination method and measurements are recorded in "
            f"the accompanying report and evidence log."
        )
    elif ev and ev["label"] == "VERIFIED":
        parts.append(
            "The evidence submitted by the cardholder passed forensic examination and is "
            "treated as authentic. It is included for completeness."
        )
    parts.append(strategy["winning_argument"])
    return " ".join(parts)
