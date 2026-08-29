"""Run the CE 3.0 rulebook over REAL chargebacks from IEEE-CIS.

Run: python -m scripts.qualify_real

This is the honest headline of the whole project: Visa's published Compelling Evidence 3.0
gate, applied unmodified to 590,540 real transactions and 20,663 real reported chargebacks.
No model, no simulation -- a rules computation whose result is a fact about the data.

Two constraints are stated rather than smoothed over, and both push the measured rate DOWN
relative to reality:

  * The dataset anonymises network identifiers, so purchase IP is unavailable and the
    "two Main elements" qualification path cannot be assessed. Every qualifying case here
    must go through "one Main + one Secondary".
  * The training period spans 182 days, so a 120-day lookback exists only for disputes
    raised on day 120 or later. Those are the denominator; earlier disputes are excluded as
    unassessable rather than counted as failures.

`--sensitivity` re-runs the gate across relaxed prior-age floors, which separates "the
merchant's data is missing" from "the dataset is too short".
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules.ce3 import v2026_04 as ce3  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

GATE_COLS = [
    "txn_id", "card_token", "ts", "status", "disputed", "tc40_reported",
    "is_validation_charge", "product_description", "merchandise_or_services",
    "purchase_ip", "device_fingerprint", "device_id", "customer_email",
    "customer_account_id", "shipping_address",
]


def build_index(ledger: pd.DataFrame) -> tuple[dict, dict]:
    """Index the ledger once.

    Grouping 590,540 rows into per-customer record lists costs minutes, so it is hoisted
    out of `run` -- the sensitivity sweep calls the gate five times and rebuilding the index
    each time made the script appear to hang.
    """
    hist = {
        cid: g[GATE_COLS].to_dict("records")
        for cid, g in ledger[GATE_COLS + ["customer_id"]].groupby("customer_id")
    }
    by_txn = {r["txn_id"]: r for r in ledger[GATE_COLS].to_dict("records")}
    return hist, by_txn


def run(min_age: int, max_age: int, hist: dict, by_txn: dict, disputes: pd.DataFrame,
        verbose: bool = True) -> tuple[pd.DataFrame, dict]:
    # The rulebook reads its age window from module constants; override them for the
    # sensitivity sweep rather than duplicating the matching logic here.
    orig = (ce3.PRIOR_MIN_AGE_DAYS, ce3.PRIOR_MAX_AGE_DAYS)
    ce3.PRIOR_MIN_AGE_DAYS, ce3.PRIOR_MAX_AGE_DAYS = min_age, max_age
    try:
        rows = []
        for rec in disputes.to_dict("records"):
            d = dict(by_txn[rec["txn_id"]])
            d["disputed"] = False  # as it stood when raised
            h = [x for x in hist.get(rec["customer_id"], []) if x["txn_id"] != rec["txn_id"]]
            q = ce3.qualify(d, h, reason_code="10.4")
            rows.append({
                "txn_id": rec["txn_id"],
                "customer_id": rec["customer_id"],
                "amount_inr": rec["amount_inr"],
                "qualified": q.qualified,
                "naive_qualified": q.naive_rule_qualified,
                "n_matched": len(q.matched_elements),
                "matched_elements": ",".join(q.matched_elements),
                "candidate_priors": q.candidate_prior_count,
                "primary_gap": q.blocking_gaps[0].code if q.blocking_gaps else None,
                "unlock": ",".join(q.unlock_elements),
            })
        return pd.DataFrame(rows), {"min_age": min_age, "max_age": max_age}
    finally:
        ce3.PRIOR_MIN_AGE_DAYS, ce3.PRIOR_MAX_AGE_DAYS = orig


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensitivity", action="store_true")
    args = ap.parse_args()

    ledger = pd.read_parquet(DATA / "real_ledger.parquet")
    disputes = pd.read_parquet(DATA / "real_disputes.parquet")
    assessable = disputes[disputes["assessable"]].copy()

    print(f"real transactions : {len(ledger):,}")
    print(f"real chargebacks  : {len(disputes):,}")
    print(f"assessable        : {len(assessable):,} (raised on day >= 120)\n")

    print("indexing ledger ...", flush=True)
    hist, by_txn = build_index(ledger)
    print(f"  indexed {len(hist):,} customer histories\n", flush=True)

    res, _ = run(120, 364, hist, by_txn, assessable)
    res.to_parquet(DATA / "real_qualification.parquet", index=False)

    q = res["qualified"]
    n = len(res)
    print("=" * 68)
    print("CE 3.0 GATE ON REAL CHARGEBACKS  (rulebook ce3/2026.04, unmodified)")
    print("=" * 68)
    print(f"  qualified                    {q.sum():>6,}  ({q.mean():.2%})")
    print(f"  value defensible             Rs {res.loc[q, 'amount_inr'].sum():>14,.0f}")
    print(f"  value NOT defensible         Rs {res.loc[~q, 'amount_inr'].sum():>14,.0f}")
    nv = res["naive_qualified"]
    print(f"\n  naive 'any 2 of 4' would say {nv.sum():>6,}  ({nv.mean():.2%})")
    fp = (~q) & nv
    print(f"  naive FALSE POSITIVES        {fp.sum():>6,}  worth Rs {res.loc[fp, 'amount_inr'].sum():,.0f}")

    # The funnel matters more than any single rate: it separates "this merchant never
    # captured the data" from "this cardholder has no relationship with the merchant at
    # all", and only the first is something a merchant can fix.
    gaps = Counter(res["primary_gap"].dropna())
    no_hist = gaps.get("NO_PRIOR_HISTORY", 0) + gaps.get("INSUFFICIENT_ELIGIBLE_PRIORS", 0)
    reached = n - no_hist
    anchor = gaps.get("NO_MAIN_ANCHOR", 0)
    print("\n  funnel:")
    print(f"    assessable chargebacks                {n:>6,}")
    print(f"    -> clear the prior-history gate       {reached:>6,}  ({reached / n:.1%})")
    print(f"    -> qualify on elements                {q.sum():>6,}  ({q.sum() / max(reached, 1):.1%} of those)")
    print(f"    blocked ONLY by a missing Main anchor {anchor:>6,}  ({anchor / max(reached, 1):.1%} of those)")
    out_funnel = {
        "assessable": int(n), "cleared_prior_gate": int(reached),
        "qualified": int(q.sum()), "blocked_no_main_anchor": int(anchor),
    }

    print("\n  blocking gaps:")
    for code, c in Counter(res["primary_gap"].dropna()).most_common(6):
        print(f"    {code:34s} {c:>6,}  ({c / n:.1%})")

    unlocks = Counter()
    for u in res.loc[~q, "unlock"]:
        for e in [x for x in str(u).split(",") if x]:
            unlocks[e] += 1
    if unlocks:
        print("\n  single-element unlocks:")
        for e, c in unlocks.most_common(5):
            print(f"    {e:26s} {c:>6,}")

    out = {
        "n_transactions": int(len(ledger)),
        "n_chargebacks": int(len(disputes)),
        "n_assessable": int(n),
        "qualified": int(q.sum()),
        "qualified_rate": float(q.mean()),
        "naive_qualified_rate": float(nv.mean()),
        "naive_false_positives": int(fp.sum()),
        "naive_false_positive_inr": float(res.loc[fp, "amount_inr"].sum()),
        "defensible_inr": float(res.loc[q, "amount_inr"].sum()),
        "undefendable_inr": float(res.loc[~q, "amount_inr"].sum()),
        "funnel": out_funnel,
        "blocking_gaps": {k: int(v) for k, v in gaps.most_common()},
        "capture_coverage": {
            c: float(ledger[c].notna().mean())
            for c in ["device_fingerprint", "device_id", "purchase_ip", "customer_email",
                      "shipping_address", "customer_account_id"]
        },
        "constraints": {
            "purchase_ip": "anonymised by the data provider; the 2-Main path is unassessable",
            "account_id": "no merchant-side account identifier in this dataset",
            "span_days": 182,
            "note": "Both constraints push the measured qualification rate BELOW reality.",
        },
    }

    if args.sensitivity:
        print("\n  sensitivity to the prior-age floor (isolates dataset span from data gaps):")
        sens = {}
        for lo in (120, 90, 60, 30, 14):
            r, _ = run(lo, 364, hist, by_txn, assessable, verbose=False)
            sens[lo] = {"qualified": int(r.qualified.sum()), "rate": float(r.qualified.mean())}
            print(f"    priors >= {lo:>3}d : {r.qualified.sum():>6,} qualify  ({r.qualified.mean():.2%})")
        out["sensitivity"] = sens

    (ROOT / "docs" / "real_qualification.json").write_text(json.dumps(out, indent=2))
    print(f"\nsaved -> {DATA / 'real_qualification.parquet'}")
    print(f"saved -> {ROOT / 'docs' / 'real_qualification.json'}")


if __name__ == "__main__":
    main()
