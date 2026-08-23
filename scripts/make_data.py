"""Generate the synthetic ledger, run the CE 3.0 rulebook over every dispute, and draw outcomes.

Run: python -m scripts.make_data
Outputs to data/: ledger.parquet, disputes.parquet, cases.parquet
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules import registry  # noqa: E402
from synth import ledger as synth_ledger  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"

# Columns the CE 3.0 gate reads. Passing narrow dicts rather than whole rows keeps the
# per-dispute loop fast enough to run over the full ledger in seconds.
TXN_COLS = [
    "txn_id", "card_token", "ts", "status", "disputed", "tc40_reported",
    "is_validation_charge", "product_description", "merchandise_or_services",
    "purchase_ip", "device_fingerprint", "device_id", "customer_email",
    "customer_account_id", "shipping_address",
]


def main() -> None:
    t0 = time.time()
    DATA.mkdir(exist_ok=True)

    print("generating ledger ...")
    ledger, disputes = synth_ledger.generate()
    print(f"  {len(ledger):,} transactions  {ledger.customer_id.nunique():,} customers")
    print(f"  {len(disputes):,} disputes")

    ce3 = registry.ce3()

    # Index history by customer once; the gate is then a dict lookup per dispute.
    hist_cols = ledger[TXN_COLS + ["customer_id"]]
    by_cust: dict[str, list[dict]] = {
        cid: g[TXN_COLS].to_dict("records") for cid, g in hist_cols.groupby("customer_id")
    }
    txn_by_id = ledger.set_index("txn_id")

    print("running CE 3.0 rulebook ...")
    results = []
    for rec in disputes.to_dict("records"):
        txn = txn_by_id.loc[rec["txn_id"]]
        # txn_id is the index here, so it is read from the record rather than the row.
        disputed = {c: txn[c] for c in TXN_COLS if c != "txn_id"}
        disputed["txn_id"] = rec["txn_id"]
        # The gate must see the transaction as it stood when the dispute was raised, not
        # flagged as already-disputed by its own dispute.
        disputed["disputed"] = False
        history = [t for t in by_cust[rec["customer_id"]] if t["txn_id"] != rec["txn_id"]]
        q = ce3.qualify(disputed, history, reason_code=rec["reason_code"])

        results.append(
            {
                "dispute_id": rec["dispute_id"],
                "qualified": q.qualified,
                "n_matched": len(q.matched_elements),
                "matched_elements": ",".join(q.matched_elements),
                "n_main_matched": sum(
                    1 for e in q.matched_elements if ce3.ELEMENT_TIER.get(e) == "main"
                ),
                "candidate_prior_count": q.candidate_prior_count,
                "primary_gap": q.blocking_gaps[0].code if q.blocking_gaps else None,
                "unlock_elements": ",".join(q.unlock_elements),
                "naive_rule_qualified": q.naive_rule_qualified,
                "rule_version": q.rule_version,
            }
        )

    qdf = pd.DataFrame(results)
    cases = disputes.merge(qdf, on="dispute_id", how="left")

    # Attach the transaction-level signals the win model and the intent model consume.
    txn_feats = ledger.set_index("txn_id")[
        ["category", "merchandise_or_services", "channel", "descriptor_is_clear",
         "delivery_confirmed", "threeds_status", "avs_match", "cvv_match",
         "post_purchase_usage_days", "order_index", "customer_id"]
    ]
    cases = cases.merge(
        txn_feats.drop(columns=["customer_id"]), left_on="txn_id", right_index=True, how="left"
    )

    print("drawing outcomes ...")
    cases["won_if_represented"] = synth_ledger.draw_win_outcomes(
        cases,
        qualified=cases["qualified"].to_numpy(),
        n_matched=cases["n_matched"].to_numpy(),
        delivery_confirmed=cases["delivery_confirmed"].to_numpy(),
        descriptor_clear=cases["descriptor_is_clear"].to_numpy(),
        threeds_auth=(cases["threeds_status"] == "authenticated").to_numpy(),
    )

    ledger.to_parquet(DATA / "ledger.parquet", index=False)
    disputes.to_parquet(DATA / "disputes.parquet", index=False)
    cases.to_parquet(DATA / "cases.parquet", index=False)

    _report(cases, ledger)
    print(f"\ndone in {time.time() - t0:.1f}s -> {DATA}")


def _report(cases: pd.DataFrame, ledger: pd.DataFrame) -> None:
    c104 = cases[cases.reason_code == "10.4"]
    print("\n--- ledger sanity ---")
    for col in ["purchase_ip", "device_fingerprint", "device_id", "customer_email",
                "customer_account_id", "shipping_address", "product_description"]:
        print(f"  capture {col:22s} {ledger[col].notna().mean():6.1%}")

    print("\n--- CE 3.0 qualification ---")
    print(f"  disputes total            {len(cases):,}")
    print(f"  reason code 10.4          {len(c104):,} ({len(c104)/len(cases):.1%})")
    print(f"  qualified (real rule)     {c104.qualified.mean():.1%} of 10.4")
    print(f"  qualified (naive rule)    {c104.naive_rule_qualified.mean():.1%} of 10.4")
    over = c104[(~c104.qualified) & (c104.naive_rule_qualified)]
    print(f"  naive FALSE POSITIVES     {len(over):,} cases ({len(over)/max(1,len(c104)):.1%} of 10.4)")
    print(f"    -> rupees at stake      Rs {over.amount_inr.sum():,.0f}")

    print("\n  top blocking gaps:")
    for code, n in c104["primary_gap"].value_counts().head(6).items():
        print(f"    {code:32s} {n:5,}  ({n/len(c104):.1%})")

    print("\n  single-field unlocks (cases that would flip if captured):")
    unlocks = (
        c104[~c104.qualified]["unlock_elements"].str.split(",").explode().replace("", np.nan).dropna()
    )
    for el, n in unlocks.value_counts().head(5).items():
        print(f"    {el:26s} {n:5,}")

    print("\n--- win outcomes (structural model) ---")
    print(f"  overall win rate          {cases.won_if_represented.mean():.1%}")
    print(f"  10.4 qualified            {c104[c104.qualified].won_if_represented.mean():.1%}")
    print(f"  10.4 NOT qualified        {c104[~c104.qualified].won_if_represented.mean():.1%}")
    print("  by latent intent:")
    for intent, g in cases.groupby("latent_intent"):
        print(f"    {intent:26s} {g.won_if_represented.mean():.1%}  (n={len(g):,})")


if __name__ == "__main__":
    main()
