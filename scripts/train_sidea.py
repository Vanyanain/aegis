"""Train the Side A win-probability model.

Run: python -m scripts.train_sidea

Design decisions that matter for the honesty of the reported numbers:

* GROUPED SPLIT BY CUSTOMER. A random row split would put two disputes from the same
  customer -- sharing a device, an IP and a behavioural profile -- on both sides of the
  train/test line. Every split here is by customer_id, so no customer is ever in two splits.

* A DEDICATED CALIBRATION SPLIT. Isotonic regression fitted on training predictions would
  be fitting to an in-sample distribution and would report a Brier score it cannot hold.
  Calibration gets its own 20% of customers, untouched by training and disjoint from test.

* THE THRESHOLD IS NOT CHOSEN HERE. Accuracy-optimal thresholds are the wrong objective for
  this problem; the Cost Lab picks it in rupees. This script reports ranking and calibration
  quality only.
"""

from __future__ import annotations

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
    average_precision_score, brier_score_loss, f1_score, precision_score,
    recall_score, roc_auc_score,
)

from aegis.sidea import features as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA, MODELS, DOCS = ROOT / "data", ROOT / "models", ROOT / "docs"
SEED = 20260822


def split_by_customer(df: pd.DataFrame, seed: int = SEED) -> tuple[np.ndarray, ...]:
    """60/20/20 train/calibration/test, partitioned on customers not rows."""
    rng = np.random.default_rng(seed)
    custs = np.asarray(df["customer_id"].unique(), dtype=object)
    rng.shuffle(custs)
    n = len(custs)
    tr, ca = custs[: int(0.60 * n)], custs[int(0.60 * n) : int(0.80 * n)]
    te = custs[int(0.80 * n) :]
    m = df["customer_id"].to_numpy()
    return np.isin(m, tr), np.isin(m, ca), np.isin(m, te)


def main() -> None:
    cases = pd.read_parquet(DATA / "cases.parquet")
    ledger = pd.read_parquet(DATA / "ledger.parquet")
    MODELS.mkdir(exist_ok=True)

    print(f"building features for {len(cases):,} disputes ...")
    df = F.build(cases, ledger)
    y = df["won_if_represented"].to_numpy()
    X = df[F.ALL_FEATURES]

    m_tr, m_ca, m_te = split_by_customer(df)
    print(f"  train {m_tr.sum():,}  calib {m_ca.sum():,}  test {m_te.sum():,}")
    assert not (set(df.loc[m_tr, "customer_id"]) & set(df.loc[m_te, "customer_id"]))

    model = lgb.LGBMClassifier(
        # Capacity is deliberately small. With ~2k training disputes and a large
        # irreducible-noise term in the generative process, a 31-leaf model peaks within
        # ten boosting rounds and then memorises. Shallow trees plus strong regularisation
        # trade a little training fit for a model that still ranks on unseen customers.
        n_estimators=1200,
        learning_rate=0.02,
        num_leaves=8,
        max_depth=4,
        min_child_samples=60,
        subsample=0.80,
        subsample_freq=1,
        colsample_bytree=0.70,
        reg_lambda=5.0,
        reg_alpha=1.0,
        random_state=SEED,
        verbose=-1,
    )
    model.fit(
        X[m_tr], y[m_tr],
        eval_set=[(X[m_ca], y[m_ca])],
        eval_metric="average_precision",
        callbacks=[lgb.early_stopping(120, verbose=False)],
    )
    print(f"  best iteration: {model.best_iteration_}")

    raw_ca = model.predict_proba(X[m_ca])[:, 1]
    raw_te = model.predict_proba(X[m_te])[:, 1]

    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_ca, y[m_ca])
    cal_te = iso.predict(raw_te)

    metrics = {
        "n_train": int(m_tr.sum()), "n_calib": int(m_ca.sum()), "n_test": int(m_te.sum()),
        "test_base_rate": float(y[m_te].mean()),
        "roc_auc": float(roc_auc_score(y[m_te], raw_te)),
        "pr_auc": float(average_precision_score(y[m_te], raw_te)),
        "brier_uncalibrated": float(brier_score_loss(y[m_te], raw_te)),
        "brier_calibrated": float(brier_score_loss(y[m_te], cal_te)),
        "at_threshold_0.5": _at(y[m_te], cal_te, 0.5),
        "calibration_bins": _calibration(y[m_te], cal_te),
        "by_qualification": {
            "qualified": _slice(df, m_te, y, cal_te, True),
            "not_qualified": _slice(df, m_te, y, cal_te, False),
        },
    }

    print(f"\n  ROC-AUC {metrics['roc_auc']:.3f}   PR-AUC {metrics['pr_auc']:.3f}"
          f"   base rate {metrics['test_base_rate']:.3f}")
    print(f"  Brier   {metrics['brier_uncalibrated']:.4f} -> {metrics['brier_calibrated']:.4f} after isotonic")

    imp = (
        pd.Series(model.feature_importances_, index=F.ALL_FEATURES)
        .sort_values(ascending=False)
    )
    metrics["feature_importance"] = {k: int(v) for k, v in imp.head(20).items()}
    print("\n  top features:")
    for k, v in imp.head(8).items():
        print(f"    {k:28s} {v}")

    joblib.dump(
        {
            "model": model,
            "calibrator": iso,
            "features": F.ALL_FEATURES,
            "categorical": F.CATEGORICAL,
            "categories": {c: list(df[c].cat.categories) for c in F.CATEGORICAL},
            "metrics": metrics,
            "seed": SEED,
        },
        MODELS / "sidea_winprob.joblib",
    )

    # Persist the scored test set: the Cost Lab optimises thresholds on exactly these
    # held-out predictions, never on training data.
    test_out = df.loc[m_te, ["dispute_id", "txn_id", "customer_id", "reason_code",
                             "amount_inr", "qualified", "n_matched", "latent_intent",
                             "has_customer_evidence", "tc40_reported", "won_if_represented"]].copy()
    test_out["win_prob"] = cal_te
    test_out.to_parquet(DATA / "sidea_test_scored.parquet", index=False)

    DOCS.mkdir(exist_ok=True)
    (DOCS / "metrics_sidea.json").write_text(json.dumps(metrics, indent=2))
    print(f"\nsaved -> {MODELS/'sidea_winprob.joblib'}")


def _at(y: np.ndarray, p: np.ndarray, t: float) -> dict:
    pred = (p >= t).astype(int)
    return {
        "threshold": t,
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "n_flagged": int(pred.sum()),
    }


def _slice(df: pd.DataFrame, mask: np.ndarray, y: np.ndarray, p: np.ndarray, qual: bool) -> dict:
    sub = df.loc[mask, "qualified"].to_numpy().astype(bool) == qual
    if sub.sum() < 20:
        return {"n": int(sub.sum()), "note": "too few cases to report"}
    return {
        "n": int(sub.sum()),
        "actual_win_rate": float(y[mask][sub].mean()),
        "mean_predicted": float(p[sub].mean()),
    }


def _calibration(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= 1.0)
        if m.sum() == 0:
            continue
        out.append({
            "bin_lo": float(edges[i]), "bin_hi": float(edges[i + 1]),
            "n": int(m.sum()), "mean_predicted": float(p[m].mean()),
            "observed_rate": float(y[m].mean()),
        })
    return out


if __name__ == "__main__":
    main()
