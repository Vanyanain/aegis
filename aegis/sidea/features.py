"""Side A feature construction.

Two rules govern this module, and both exist to keep the evaluation honest:

1. `latent_intent` and `issuer_effect` are generative-process internals. They created the
   labels and they are NEVER features. Any model that saw them would be reading the answer
   key. `FORBIDDEN` below is enforced at build time, not by convention.

2. Every feature here is something a real merchant actually has at dispute time. No feature
   depends on the dispute outcome, on data that arrives after representment, or on anything
   the merchant would have to buy from the network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Columns that leak the generative process or the outcome. Never fed to a model.
FORBIDDEN = frozenset(
    {"latent_intent", "issuer_effect", "won_if_represented", "dispute_id", "txn_id",
     "customer_id", "card_token", "dispute_date", "matched_elements", "unlock_elements",
     "rule_version", "primary_gap", "naive_rule_qualified", "issuer"}
)

CATEGORICAL = ["category", "merchandise_or_services", "channel", "threeds_status", "reason_code"]

NUMERIC = [
    "qualified",
    "n_matched",
    "n_main_matched",
    "candidate_prior_count",
    "amount_inr",
    "log_amount",
    "descriptor_is_clear",
    "delivery_confirmed",
    "avs_match",
    "cvv_match",
    "threeds_authenticated",
    "post_purchase_usage_days",
    "order_index",
    "days_to_dispute",
    "customer_txn_count",
    "customer_tenure_days",
    "customer_prior_disputes",
    "customer_dispute_rate",
    "customer_total_spend",
    "amount_vs_customer_mean",
    "is_subscription",
    "has_customer_evidence",
    "tc40_reported",
]

ALL_FEATURES = NUMERIC + CATEGORICAL


def build(cases: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Assemble the Side A feature frame.

    Behavioural aggregates are computed from the customer's history AS OF the dispute date.
    Using the customer's full lifetime history would leak future orders into a case that was
    decided before they happened -- a subtle temporal leak that inflates metrics and would
    not survive contact with production.
    """
    df = cases.copy()

    df["log_amount"] = np.log1p(df["amount_inr"])
    df["threeds_authenticated"] = (df["threeds_status"] == "authenticated").astype(int)
    df["is_subscription"] = (df["channel"] == "subscription_rebill").astype(int)

    txn_ts = ledger.set_index("txn_id")["ts"]
    df["days_to_dispute"] = (
        pd.to_datetime(df["dispute_date"]) - df["txn_id"].map(txn_ts)
    ).dt.days.fillna(0)

    df = _attach_customer_history(df, ledger)

    for c in ("qualified", "descriptor_is_clear", "delivery_confirmed", "avs_match",
              "cvv_match", "has_customer_evidence", "tc40_reported"):
        df[c] = df[c].astype(int)

    for c in CATEGORICAL:
        df[c] = df[c].astype("category")

    leaked = FORBIDDEN & set(ALL_FEATURES)
    if leaked:
        raise RuntimeError(f"leakage: forbidden columns in feature list: {sorted(leaked)}")

    return df


def _attach_customer_history(df: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time customer aggregates, evaluated strictly before each dispute date."""
    led = ledger[["customer_id", "txn_id", "ts", "amount_inr", "disputed", "dispute_date"]].copy()
    led = led.sort_values(["customer_id", "ts"])

    out = {
        "customer_txn_count": [], "customer_tenure_days": [], "customer_prior_disputes": [],
        "customer_total_spend": [], "amount_vs_customer_mean": [],
    }
    by_cust = {cid: g for cid, g in led.groupby("customer_id")}

    for cid, dd, amt in zip(df["customer_id"], pd.to_datetime(df["dispute_date"]), df["amount_inr"]):
        g = by_cust.get(cid)
        if g is None or g.empty:
            out["customer_txn_count"].append(0)
            out["customer_tenure_days"].append(0)
            out["customer_prior_disputes"].append(0)
            out["customer_total_spend"].append(0.0)
            out["amount_vs_customer_mean"].append(1.0)
            continue
        past = g[g["ts"] < dd]
        n = len(past)
        out["customer_txn_count"].append(n)
        out["customer_tenure_days"].append((dd - past["ts"].min()).days if n else 0)
        prior_d = past[past["disputed"] & (pd.to_datetime(past["dispute_date"]) < dd)]
        out["customer_prior_disputes"].append(len(prior_d))
        spend = float(past["amount_inr"].sum()) if n else 0.0
        out["customer_total_spend"].append(spend)
        mean_amt = float(past["amount_inr"].mean()) if n else amt
        out["amount_vs_customer_mean"].append(float(amt) / mean_amt if mean_amt > 0 else 1.0)

    for k, v in out.items():
        df[k] = v

    df["customer_dispute_rate"] = np.where(
        df["customer_txn_count"] > 0,
        df["customer_prior_disputes"] / df["customer_txn_count"].clip(lower=1),
        0.0,
    )
    return df
