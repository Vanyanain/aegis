"""Train and honestly evaluate the Side B evidence-authenticity detector.

Run: python -m scripts.train_sideb

Three evaluations, because a single held-out number would overstate what this model knows:

1. HELD-OUT.  Standard split, grouped by customer so the same person's evidence never
   appears on both sides. Reports precision, recall, F1, PR-AUC, ROC-AUC and Brier, plus
   per-family recall -- because "89% recall" averaged over four families hides which
   families it actually fails on.

2. LEAVE-ONE-FAMILY-OUT.  Train on three fake families, test on the fourth, unseen. This is
   the only honest answer to "will it catch a generator you have never seen?", and it is
   where most document-forensics demos quietly fall apart.

3. METADATA ABLATION.  Retrain with the EXIF/provenance group removed. A forger who strips
   or spoofs metadata deletes that entire group for free, so performance without it is the
   floor the merchant should actually plan around.

The human baseline is the 30-annotator study in GPT4o-Receipt (arXiv 2603.11442):
accuracy 0.797, recall 0.770, FPR 0.120. Beating it is the claim; every number below is
reported against it, including where the model loses.
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
    average_precision_score, brier_score_loss, confusion_matrix, f1_score,
    precision_score, recall_score, roc_auc_score,
)

from aegis.sideb import forensics as FX  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA, MODELS, DOCS = ROOT / "data", ROOT / "models", ROOT / "docs"
SEED = 20260822

HUMAN_BASELINE = {"source": "GPT4o-Receipt, arXiv 2603.11442, 30 annotators",
                  "accuracy": 0.797, "recall": 0.770, "fpr": 0.120, "f1": 0.852}

# Columns the detector must never see. Beyond the obvious label leaks, `delivery` is
# ground truth about how the file travelled -- knowing a file came via WhatsApp explains
# away its missing EXIF, and no production system is handed that fact.
NON_FEATURES = {
    "item_id", "path", "family", "is_fake", "txn_id", "customer_id",
    "claimed_amount_inr", "claimed_ts", "claimed_descriptor",
    "receipt_total", "receipt_ts", "receipt_merchant", "delivery",
    "arithmetic_broken", "broken_fields", "error", "extract_error",
}


def feature_cols(df: pd.DataFrame, drop_groups: set[str] | None = None) -> list[str]:
    drop_groups = drop_groups or set()
    cols = []
    for c in df.columns:
        if c in NON_FEATURES or not pd.api.types.is_numeric_dtype(df[c]):
            continue
        if FX.group_of(c) in drop_groups:
            continue
        cols.append(c)
    return cols


def _params() -> dict:
    return dict(
        n_estimators=900, learning_rate=0.03, num_leaves=24, max_depth=6,
        min_child_samples=25, subsample=0.85, subsample_freq=1, colsample_bytree=0.75,
        reg_lambda=2.0, random_state=SEED, verbose=-1,
    )


def split_by_customer(df: pd.DataFrame, seed: int = SEED):
    rng = np.random.default_rng(seed)
    custs = np.asarray(df["customer_id"].unique(), dtype=object)
    rng.shuffle(custs)
    n = len(custs)
    tr, ca, te = custs[: int(0.6 * n)], custs[int(0.6 * n) : int(0.8 * n)], custs[int(0.8 * n) :]
    m = df["customer_id"].to_numpy()
    return np.isin(m, tr), np.isin(m, ca), np.isin(m, te)


def fit_eval(df: pd.DataFrame, cols: list[str], masks) -> tuple:
    m_tr, m_ca, m_te = masks
    y = df["is_fake"].to_numpy()
    X = df[cols]
    model = lgb.LGBMClassifier(**_params())
    model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_ca], y[m_ca])], eval_metric="average_precision",
              callbacks=[lgb.early_stopping(80, verbose=False)])
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(model.predict_proba(X[m_ca])[:, 1], y[m_ca])
    p_te = iso.predict(model.predict_proba(X[m_te])[:, 1])
    return model, iso, y[m_te], p_te


def report(y: np.ndarray, p: np.ndarray, thr: float = 0.5) -> dict:
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "threshold": thr, "n": int(len(y)),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "accuracy": float((pred == y).mean()),
        "fpr": float(fp / max(tn + fp, 1)),
        "roc_auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else float("nan"),
        "pr_auc": float(average_precision_score(y, p)) if len(set(y)) > 1 else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def main() -> None:
    df = pd.read_parquet(DATA / "evidence_features.parquet")
    df = df[df.get("extract_error").isna()] if "extract_error" in df else df
    df = df.fillna({c: 0.0 for c in df.columns if pd.api.types.is_numeric_dtype(df[c])})
    MODELS.mkdir(exist_ok=True)

    cols = feature_cols(df)
    masks = split_by_customer(df)
    print(f"{len(df):,} items, {len(cols)} features")
    print(f"  train {masks[0].sum():,}  calib {masks[1].sum():,}  test {masks[2].sum():,}")

    # --- 1. Held-out ----------------------------------------------------------------
    model, iso, y_te, p_te = fit_eval(df, cols, masks)
    main_report = report(y_te, p_te)
    print(f"\n[held-out]  precision {main_report['precision']:.3f}  recall {main_report['recall']:.3f}"
          f"  F1 {main_report['f1']:.3f}  PR-AUC {main_report['pr_auc']:.3f}  FPR {main_report['fpr']:.3f}")
    print(f"            vs human  recall {HUMAN_BASELINE['recall']:.3f}  FPR {HUMAN_BASELINE['fpr']:.3f}"
          f"  accuracy {HUMAN_BASELINE['accuracy']:.3f}")

    # Per-family recall: an average over families hides which ones fail.
    te = df[masks[2]].copy()
    te["p"] = p_te
    per_family = {}
    for fam, g in te.groupby("family"):
        if fam == "genuine":
            per_family[fam] = {"n": int(len(g)), "false_positive_rate": float((g["p"] >= 0.5).mean())}
        else:
            per_family[fam] = {"n": int(len(g)), "recall": float((g["p"] >= 0.5).mean())}
    print("\n  per-family:")
    for fam, r in per_family.items():
        k = "recall" if "recall" in r else "false_positive_rate"
        print(f"    {fam:16s} n={r['n']:4d}  {k}={r[k]:.3f}")

    # --- 2. Leave-one-family-out ----------------------------------------------------
    print("\n[leave-one-family-out]  train on 3 fake families, test on the unseen 4th")
    lofo = {}
    m_tr_c, m_ca_c, m_te_c = masks
    for held in ["ai_generated", "digital_edit", "template_forge", "recycled"]:
        # Train on every family EXCEPT the held-out one, using train+calib customers.
        tr = df[(df["family"] != held) & (m_tr_c | m_ca_c)]
        if tr["is_fake"].nunique() < 2:
            continue
        # Test on the entire unseen family plus genuine receipts from held-out customers.
        # Using the whole family is safe precisely because the model never saw ANY example
        # of it; the genuine half is customer-disjoint from training as usual.
        ev = pd.concat([df[df["family"] == held], df[m_te_c & (df["family"] == "genuine")]])
        if len(ev) < 40 or ev["is_fake"].nunique() < 2:
            continue
        m = lgb.LGBMClassifier(**_params())
        m.fit(tr[cols], tr["is_fake"].to_numpy())
        p = m.predict_proba(ev[cols])[:, 1]
        r = report(ev["is_fake"].to_numpy(), p)
        lofo[held] = r
        print(f"    unseen={held:16s} recall {r['recall']:.3f}  FPR {r['fpr']:.3f}"
              f"  PR-AUC {r['pr_auc']:.3f}  (n={r['n']})")

    # --- 3. Metadata ablation -------------------------------------------------------
    print("\n[ablation]  what survives when a forger strips or spoofs EXIF")
    ablations = {}
    for name, drop in [("full", set()), ("no_provenance", {"provenance"}),
                       ("no_crosscheck", {"crosscheck"}),
                       ("no_provenance_no_crosscheck", {"provenance", "crosscheck"}),
                       ("arithmetic_only", {"provenance", "compression", "noise", "typography",
                                            "crosscheck", "file"})]:
        c = feature_cols(df, drop_groups=drop)
        _, _, yy, pp = fit_eval(df, c, masks)
        r = report(yy, pp)
        ablations[name] = {"n_features": len(c), **r}
        print(f"    {name:30s} {len(c):3d} feats  recall {r['recall']:.3f}  "
              f"precision {r['precision']:.3f}  PR-AUC {r['pr_auc']:.3f}")

    # --- 4. Rules only, and rules + model combined -----------------------------------
    print("\n[layers]  which layer actually carries the result")
    from aegis.sideb import rules as ER

    te_rows = df[masks[2]].to_dict("records")
    rule_pred, rule_high = [], []
    for r in te_rows:
        flags = ER.evaluate(r, r.get("claimed_amount_inr"), r.get("claimed_ts"),
                            bool(r.get("descriptor_is_clear", True)))
        worst = max((ER.SEVERITY_ORDER[f.severity] for f in flags), default=-1)
        rule_high.append(worst >= ER.SEVERITY_ORDER["high"])
        rule_pred.append(worst >= ER.SEVERITY_ORDER["medium"])
    rule_high = np.asarray(rule_high)
    y_true = df.loc[masks[2], "is_fake"].to_numpy()

    rules_only = report(y_true, rule_high.astype(float))
    combined_p = np.maximum(p_te, rule_high.astype(float))
    combined = report(y_true, combined_p)
    layers = {"model_only": main_report, "rules_only": rules_only, "combined": combined}
    for k, r in layers.items():
        print(f"    {k:14s} recall {r['recall']:.3f}  precision {r['precision']:.3f}  "
              f"FPR {r['fpr']:.3f}  F1 {r['f1']:.3f}")

    # Per-family recall for the combined layer -- this is what a merchant actually gets.
    te["rule_high"] = rule_high
    te["combined"] = combined_p
    combined_family = {}
    for fam, g in te.groupby("family"):
        key = "false_positive_rate" if fam == "genuine" else "recall"
        combined_family[fam] = {"n": int(len(g)), key: float((g["combined"] >= 0.5).mean())}
    print("\n  combined, per family:")
    for fam, r in combined_family.items():
        k = "recall" if "recall" in r else "false_positive_rate"
        print(f"    {fam:16s} n={r['n']:4d}  {k}={r[k]:.3f}")

    imp = pd.Series(model.feature_importances_, index=cols).sort_values(ascending=False)
    by_group = {}
    for k, v in imp.items():
        by_group[FX.group_of(k)] = by_group.get(FX.group_of(k), 0) + int(v)
    print("\n  importance by feature group:")
    for g, v in sorted(by_group.items(), key=lambda kv: -kv[1]):
        print(f"    {g:14s} {v:6d}")
    print("\n  top features:")
    for k, v in imp.head(10).items():
        print(f"    {k:32s} {int(v)}")

    metrics = {
        "held_out": main_report,
        "layers": layers,
        "combined_per_family": combined_family,
        "per_family": per_family,
        "leave_one_family_out": lofo,
        "ablations": ablations,
        "human_baseline": HUMAN_BASELINE,
        "beats_human_recall": bool(main_report["recall"] > HUMAN_BASELINE["recall"]),
        "beats_human_fpr": bool(main_report["fpr"] < HUMAN_BASELINE["fpr"]),
        "feature_importance": {k: int(v) for k, v in imp.head(25).items()},
        "importance_by_group": by_group,
        "n_features": len(cols),
    }
    joblib.dump({"model": model, "calibrator": iso, "features": cols, "metrics": metrics,
                 "seed": SEED}, MODELS / "sideb_forensics.joblib")
    DOCS.mkdir(exist_ok=True)
    (DOCS / "metrics_sideb.json").write_text(json.dumps(metrics, indent=2))

    te[["item_id", "family", "is_fake", "p", "txn_id", "customer_id"]].to_parquet(
        DATA / "sideb_test_scored.parquet", index=False)
    print(f"\nsaved -> {MODELS / 'sideb_forensics.joblib'}")


if __name__ == "__main__":
    main()
