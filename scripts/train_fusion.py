"""Train the Genuine-Intent classifier (M3).

Run: python -m scripts.train_fusion

Three-class problem over the same non-leaky feature set Side A uses. `latent_intent` is the
TARGET here, which is the one legitimate use of it -- it is never a feature anywhere.

Reported per class, not just overall: the three classes have very different costs. Missing
`genuine_service_failure` means fighting a customer who was actually wronged, which costs a
customer as well as a case, so its recall matters more than its precision.
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
from sklearn.metrics import classification_report, log_loss, roc_auc_score  # noqa: E402

from aegis.fusion import intent as FI  # noqa: E402
from aegis.sidea import features as F  # noqa: E402
from scripts.train_sidea import split_by_customer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA, MODELS, DOCS = ROOT / "data", ROOT / "models", ROOT / "docs"
SEED = 20260822


def attach_forensics(df: pd.DataFrame) -> pd.DataFrame:
    """Join Side B's OUT-OF-FOLD tamper score onto each case.

    Out-of-fold matters: an in-sample forensic score would be sharper than anything
    available at serving time and would inflate fusion's metrics. Cases with no submitted
    evidence get 0.0, which is the correct neutral value -- absence of evidence is not
    evidence of tampering, and `has_customer_evidence` already carries the presence signal
    separately so the model can tell the two situations apart.
    """
    path = DATA / "evidence_scored.parquet"
    if not path.exists():
        df["evidence_tamper_score"] = 0.0
        return df
    ev = pd.read_parquet(path)
    ev = ev.drop_duplicates("txn_id").set_index("txn_id")["tamper_score_oof"]
    df["evidence_tamper_score"] = df["txn_id"].map(ev).fillna(0.0).astype(float)
    return df


def main() -> None:
    cases = pd.read_parquet(DATA / "cases.parquet")
    ledger = pd.read_parquet(DATA / "ledger.parquet")
    df = F.build(cases, ledger)
    df = attach_forensics(df)

    cols = FI.FEATURES + FI.CATEGORICAL
    y = pd.Categorical(df["latent_intent"], categories=list(FI.INTENTS)).codes
    X = df[cols]

    m_tr, m_ca, m_te = split_by_customer(df, seed=SEED)
    print(f"{len(df):,} disputes  train {m_tr.sum():,}  calib {m_ca.sum():,}  test {m_te.sum():,}")

    model = lgb.LGBMClassifier(
        objective="multiclass", num_class=len(FI.INTENTS),
        n_estimators=900, learning_rate=0.03, num_leaves=12, max_depth=5,
        min_child_samples=40, subsample=0.85, subsample_freq=1, colsample_bytree=0.75,
        reg_lambda=3.0, random_state=SEED, verbose=-1,
        # Balanced weights, deliberately. criminal_fraud is only ~13% of disputes, and
        # without weighting the argmax never selects it -- the model ranked it at 0.718
        # ROC-AUC while scoring 0.000 precision and recall, because the majority classes
        # always won. The costs here are not symmetric: predicting first_party_misuse for a
        # cardholder whose card was genuinely stolen means pursuing a fraud victim. Trading
        # a few points of overall accuracy for a class that is actually predicted is the
        # right trade, and macro F1 rather than accuracy is the metric that shows it.
        class_weight="balanced",
    )
    model.fit(X[m_tr], y[m_tr], eval_set=[(X[m_ca], y[m_ca])], eval_metric="multi_logloss",
              callbacks=[lgb.early_stopping(80, verbose=False)])

    p_te = model.predict_proba(X[m_te])
    pred = p_te.argmax(axis=1)
    rep = classification_report(y[m_te], pred, target_names=list(FI.INTENTS),
                                output_dict=True, zero_division=0)

    print(f"\n  accuracy {rep['accuracy']:.3f}   log-loss {log_loss(y[m_te], p_te):.4f}")
    print(f"  macro F1 {rep['macro avg']['f1-score']:.3f}")
    print("\n  per class:")
    for k in FI.INTENTS:
        r = rep[k]
        print(f"    {k:26s} P {r['precision']:.3f}  R {r['recall']:.3f}  F1 {r['f1-score']:.3f}"
              f"  (n={int(r['support'])})")

    aucs = {}
    for i, name in enumerate(FI.INTENTS):
        yb = (y[m_te] == i).astype(int)
        if yb.sum() > 5:
            aucs[name] = float(roc_auc_score(yb, p_te[:, i]))
    print("\n  one-vs-rest ROC-AUC:")
    for k, v in aucs.items():
        print(f"    {k:26s} {v:.3f}")

    # Base rates matter for reading the above: a class at 62% prevalence needs to beat 0.62.
    base = {k: float((df["latent_intent"] == k).mean()) for k in FI.INTENTS}

    metrics = {
        "n_test": int(m_te.sum()), "accuracy": float(rep["accuracy"]),
        "macro_f1": float(rep["macro avg"]["f1-score"]),
        "log_loss": float(log_loss(y[m_te], p_te)),
        "per_class": {k: rep[k] for k in FI.INTENTS},
        "ovr_roc_auc": aucs, "base_rates": base,
    }
    joblib.dump({"model": model, "features": cols, "intents": list(FI.INTENTS),
                 "categories": {c: list(df[c].cat.categories) for c in FI.CATEGORICAL},
                 "metrics": metrics, "seed": SEED}, MODELS / "fusion_intent.joblib")
    (DOCS / "metrics_fusion.json").write_text(json.dumps(metrics, indent=2))
    print(f"\nsaved -> {MODELS / 'fusion_intent.joblib'}")


if __name__ == "__main__":
    main()
