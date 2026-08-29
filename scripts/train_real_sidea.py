"""Side A on REAL data: predict chargeback risk on IEEE-CIS, split by TIME.

Run: python -m scripts.train_real_sidea

WHAT CHANGED FROM THE SYNTHETIC VERSION.

The synthetic build predicted `won_if_represented`, a label drawn from a structural model we
wrote. No public dataset carries a "won the representment" outcome, so that target could
never be validated and its metrics were circular.

This predicts something real and labelled: `isFraud`, which Vesta defines as a reported
chargeback on the card. 590,540 real transactions, 3.50% positive. The win-probability model
still exists for the Cost Lab, but it is now clearly fenced as the one modelled component
rather than the whole project.

THE SPLIT IS TEMPORAL, NOT RANDOM.

A random split lets the model see transactions from the same week as its test set, and on
payment data that leaks hard -- fraud arrives in bursts that share devices, addresses and
BINs. Training on days 0-119 and testing on 150-181 is how the model would actually be
deployed: fitted on the past, scoring the future, with a gap between them so nothing
straddles the boundary. `--also-random` runs the random split too, purely to show how much
the honest protocol costs.

CE 3.0 FEATURES ARE INCLUDED ON PURPOSE.

Whether a cardholder has prior transactions on the same credential carrying a matching
device fingerprint, email or address is both (a) the CE 3.0 qualification question and
(b) genuinely predictive of whether a charge is fraudulent -- criminal card-not-present
fraud runs on thin, unmatched histories. The same computation therefore serves the rulebook
and the model, which is the architectural point of the product.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib  # noqa: E402
import lightgbm as lgb  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score,
)

ROOT = Path(__file__).resolve().parents[1]
DATA, MODELS, DOCS = ROOT / "data", ROOT / "models", ROOT / "docs"
SEED = 20260822

# Temporal protocol. A 30-day gap sits between train and test so no entity straddles it.
TRAIN_END, GAP_END, TEST_END = 120, 150, 182

RAW_NUM = [
    "TransactionAmt", "dist1", "D1", "D2", "D3", "D4", "D10", "D15",
    "C1", "C2", "C5", "C13", "C14",
    "id_01", "id_02", "id_05", "id_06", "id_09", "id_11", "id_13", "id_17",
    "id_19", "id_20",
    "V95", "V96", "V97", "V126", "V127", "V128", "V307", "V308", "V310",
]
RAW_CAT = ["ProductCD", "card4", "card6", "DeviceType", "customer_email", "channel"]

# Features AEGIS derives -- the CE 3.0 view of the customer relationship.
AEGIS_FEATS = [
    "has_device_fp", "has_email", "has_shipping",
    "cust_txn_count", "cust_prior_count", "cust_tenure_days",
    "cust_device_match_count", "cust_email_match_count", "cust_addr_match_count",
    "cust_n_main_matched", "cust_n_secondary_matched",
    "cust_amt_ratio",
]

# EXCLUDED, and the reason matters more than the feature.
#
# `cust_prior_disputes` (did this relationship have an earlier chargeback?) lifted PR-AUC
# from 0.489 to 0.846 and ROC-AUC from 0.883 to 0.979. That is not skill, it is the label.
# Vesta states that the fraud flag is applied to the user account, email and billing address
# associated with a reported chargeback -- so within a resolved entity, "a prior transaction
# was fraud" and "this transaction is fraud" are the same retrospective labelling event
# rather than a genuine temporal sequence. A merchant genuinely does know a customer's
# chargeback history in production, but on THIS dataset the feature cannot be distinguished
# from leakage, so it is dropped and the honest number is reported instead.
#
# `--with-contaminated` re-runs including it, purely to document the size of the effect.
LEAKY_FEATS = ["cust_prior_disputes"]


def build_features(led: pd.DataFrame) -> pd.DataFrame:
    """Attach point-in-time relationship features, computed strictly from the past.

    Every count below uses only transactions STRICTLY EARLIER than the row it describes.
    Computing them over a customer's whole lifetime would leak future orders into a
    decision made before those orders existed -- the most common silent leak in
    transaction modelling, and one a random split would never reveal.
    """
    df = led.sort_values(["customer_id", "day"]).copy()
    g = df.groupby("customer_id", sort=False)

    df["cust_txn_count"] = g.cumcount()
    df["cust_prior_count"] = df["cust_txn_count"]
    first_day = g["day"].transform("min")
    df["cust_tenure_days"] = df["day"] - first_day

    # Prior disputes on the relationship, excluding the current row.
    # Cast to int before the cumulative sum: `disputed` is boolean, and summing booleans
    # through a groupby transform yields an object column that LightGBM rejects.
    df["cust_prior_disputes"] = (
        g["disputed"]
        .transform(lambda s: s.astype(int).shift(1).fillna(0).cumsum())
        .astype(float)
    )

    # Does the current row's element value match ANY earlier row for this customer?
    for col, name in [("device_fingerprint", "device"), ("customer_email", "email"),
                      ("shipping_address", "addr")]:
        seen_counts = []
        for _, grp in df.groupby("customer_id", sort=False):
            seen: dict = {}
            out = []
            for v in grp[col].to_numpy():
                out.append(seen.get(v, 0) if v is not None and v == v else 0)
                if v is not None and v == v:
                    seen[v] = seen.get(v, 0) + 1
            seen_counts.extend(out)
        df[f"cust_{name}_match_count"] = seen_counts

    df["cust_n_main_matched"] = (df["cust_device_match_count"] > 0).astype(int)
    df["cust_n_secondary_matched"] = (
        (df["cust_email_match_count"] > 0).astype(int)
        + (df["cust_addr_match_count"] > 0).astype(int)
    )

    df["has_device_fp"] = df["device_fingerprint"].notna().astype(int)
    df["has_email"] = df["customer_email"].notna().astype(int)
    df["has_shipping"] = df["shipping_address"].notna().astype(int)

    mean_amt = g["TransactionAmt"].transform(lambda s: s.shift(1).expanding().mean())
    df["cust_amt_ratio"] = df["TransactionAmt"] / mean_amt.replace(0, np.nan)
    df["cust_amt_ratio"] = df["cust_amt_ratio"].fillna(1.0).clip(0, 50)

    for c in RAW_CAT:
        df[c] = df[c].astype("category")
    return df


def evaluate(y, p, name: str) -> dict:
    ap = average_precision_score(y, p)
    base = float(y.mean())
    prec, rec, thr = precision_recall_curve(y, p)
    # Operating point: the threshold that maximises F1, reported alongside a fixed
    # high-precision point because a fraud team cannot review everything.
    f1 = np.where((prec + rec) > 0, 2 * prec * rec / np.maximum(prec + rec, 1e-9), 0)
    i = int(np.nanargmax(f1))
    hi = np.where(prec >= 0.50)[0]
    hi_i = int(hi[0]) if len(hi) else i
    return {
        "split": name, "n": int(len(y)), "base_rate": base,
        "pr_auc": float(ap), "lift_over_base": float(ap / base),
        "roc_auc": float(roc_auc_score(y, p)), "brier": float(brier_score_loss(y, p)),
        "best_f1": {"f1": float(f1[i]), "precision": float(prec[i]), "recall": float(rec[i]),
                    "threshold": float(thr[min(i, len(thr) - 1)])},
        "at_precision_50": {"precision": float(prec[hi_i]), "recall": float(rec[hi_i])},
    }


def main() -> None:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--also-random", action="store_true")
    ap_.add_argument("--with-contaminated", action="store_true")
    args = ap_.parse_args()

    led = pd.read_parquet(DATA / "real_ledger.parquet")
    print(f"{len(led):,} real transactions, {led.isFraud.sum():,} chargebacks "
          f"({led.isFraud.mean():.2%})")
    print("building point-in-time relationship features ...", flush=True)
    df = build_features(led)

    feats = [c for c in RAW_NUM if c in df.columns] + RAW_CAT + AEGIS_FEATS
    y = df["isFraud"].to_numpy()

    tr = df["day"] < TRAIN_END
    te = (df["day"] >= GAP_END) & (df["day"] <= TEST_END)
    va = (df["day"] >= TRAIN_END) & (df["day"] < GAP_END)
    print(f"\ntemporal split  train(<{TRAIN_END}d)={tr.sum():,}  "
          f"gap/calib={va.sum():,}  test({GAP_END}-{TEST_END}d)={te.sum():,}")
    print(f"  fraud rate  train {y[tr].mean():.2%}  test {y[te].mean():.2%}")

    params = dict(n_estimators=1500, learning_rate=0.03, num_leaves=64, max_depth=-1,
                  min_child_samples=80, subsample=0.8, subsample_freq=1,
                  colsample_bytree=0.7, reg_lambda=5.0, random_state=SEED, verbose=-1)

    model = lgb.LGBMClassifier(**params)
    model.fit(df.loc[tr, feats], y[tr], eval_set=[(df.loc[va, feats], y[va])],
              eval_metric="average_precision",
              callbacks=[lgb.early_stopping(100, verbose=False)])
    iso = IsotonicRegression(out_of_bounds="clip").fit(
        model.predict_proba(df.loc[va, feats])[:, 1], y[va])
    p_te = iso.predict(model.predict_proba(df.loc[te, feats])[:, 1])

    temporal = evaluate(y[te], p_te, "temporal")
    print(f"\n[TEMPORAL]  PR-AUC {temporal['pr_auc']:.4f}  (base {temporal['base_rate']:.4f}, "
          f"lift {temporal['lift_over_base']:.1f}x)  ROC-AUC {temporal['roc_auc']:.4f}")
    print(f"  best-F1   P {temporal['best_f1']['precision']:.3f}  R {temporal['best_f1']['recall']:.3f}"
          f"  F1 {temporal['best_f1']['f1']:.3f}")
    print(f"  @P>=0.50  R {temporal['at_precision_50']['recall']:.3f}")

    out = {"temporal": temporal, "n_features": len(feats),
           "protocol": {"train_end_day": TRAIN_END, "gap_end_day": GAP_END,
                        "test_end_day": TEST_END}}

    # Ablation: do the CE 3.0 relationship features earn their place?
    raw_only = [c for c in feats if c not in AEGIS_FEATS]
    m2 = lgb.LGBMClassifier(**params)
    m2.fit(df.loc[tr, raw_only], y[tr], eval_set=[(df.loc[va, raw_only], y[va])],
           eval_metric="average_precision",
           callbacks=[lgb.early_stopping(100, verbose=False)])
    p2 = m2.predict_proba(df.loc[te, raw_only])[:, 1]
    no_aegis = evaluate(y[te], p2, "temporal_without_ce3_features")
    out["without_ce3_features"] = no_aegis
    print(f"\n[ABLATION]  without the CE 3.0 relationship features: "
          f"PR-AUC {no_aegis['pr_auc']:.4f}  (vs {temporal['pr_auc']:.4f})")

    if args.also_random:
        rng = np.random.default_rng(SEED)
        m = rng.random(len(df))
        rtr, rva, rte = m < 0.6, (m >= 0.6) & (m < 0.8), m >= 0.8
        m3 = lgb.LGBMClassifier(**params)
        m3.fit(df.loc[rtr, feats], y[rtr], eval_set=[(df.loc[rva, feats], y[rva])],
               eval_metric="average_precision",
               callbacks=[lgb.early_stopping(100, verbose=False)])
        p3 = m3.predict_proba(df.loc[rte, feats])[:, 1]
        rnd = evaluate(y[rte], p3, "random")
        out["random_split"] = rnd
        print(f"\n[RANDOM SPLIT]  PR-AUC {rnd['pr_auc']:.4f}  <- inflated; the same model "
              f"scores {temporal['pr_auc']:.4f} when it must predict forward in time")
        out["drift_cost_pr_auc"] = float(rnd["pr_auc"] - temporal["pr_auc"])

    if args.with_contaminated:
        cf = feats + LEAKY_FEATS
        mc = lgb.LGBMClassifier(**params)
        mc.fit(df.loc[tr, cf], y[tr], eval_set=[(df.loc[va, cf], y[va])],
               eval_metric="average_precision",
               callbacks=[lgb.early_stopping(100, verbose=False)])
        pc = mc.predict_proba(df.loc[te, cf])[:, 1]
        cont = evaluate(y[te], pc, "temporal_with_leaky_prior_dispute")
        out["contaminated_reference"] = cont
        print(f"\n[LEAKAGE CHECK]  adding cust_prior_disputes: PR-AUC {cont['pr_auc']:.4f} "
              f"vs {temporal['pr_auc']:.4f} honest. Vesta propagates the fraud label across an "
              f"account's transactions, so that feature restates the answer. Excluded.")

    imp = pd.Series(model.feature_importances_, index=feats).sort_values(ascending=False)
    out["feature_importance"] = {k: int(v) for k, v in imp.head(25).items()}
    print("\n  top features:")
    for k, v in imp.head(12).items():
        tag = "  <- AEGIS/CE3.0" if k in AEGIS_FEATS else ""
        print(f"    {k:26s} {int(v):>6}{tag}")

    MODELS.mkdir(exist_ok=True)
    joblib.dump({"model": model, "calibrator": iso, "features": feats,
                 "categories": {c: list(df[c].cat.categories) for c in RAW_CAT},
                 "metrics": out, "seed": SEED}, MODELS / "real_chargeback_risk.joblib")
    (DOCS / "metrics_real_sidea.json").write_text(json.dumps(out, indent=2))

    scored = df.loc[te, ["txn_id", "customer_id", "amount_inr", "day", "isFraud"]].copy()
    scored["risk"] = p_te
    scored.to_parquet(DATA / "real_sidea_test_scored.parquet", index=False)
    print(f"\nsaved -> {MODELS / 'real_chargeback_risk.joblib'}")


if __name__ == "__main__":
    main()
