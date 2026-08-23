"""Out-of-fold forensic scores for every evidence item.

Run: python -m scripts.score_evidence_oof  (after train_sideb)
Output: data/evidence_scored.parquet

WHY OUT-OF-FOLD. The fusion model (M3) uses the forensic tamper score as an input feature.
If that score came from a Side B model that had already seen the item during training, the
feature would be sharper on the fusion training set than it can ever be in production, and
fusion's reported metrics would be quietly inflated. Grouped k-fold means every score here
was produced by a model that never saw that item -- or any other item from the same
customer -- which is exactly the quality of signal fusion will get at serving time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lightgbm as lgb  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402

from scripts.train_sideb import _params, feature_cols  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEED = 20260822
N_FOLDS = 5


def main() -> None:
    df = pd.read_parquet(DATA / "evidence_features.parquet")
    df = df.fillna({c: 0.0 for c in df.columns if pd.api.types.is_numeric_dtype(df[c])})
    cols = feature_cols(df)
    y = df["is_fake"].to_numpy()

    # Fold assignment is by CUSTOMER, so a customer's evidence never straddles a fold.
    rng = np.random.default_rng(SEED)
    custs = np.asarray(df["customer_id"].unique(), dtype=object)
    rng.shuffle(custs)
    fold_of = {c: i % N_FOLDS for i, c in enumerate(custs)}
    folds = df["customer_id"].map(fold_of).to_numpy()

    oof = np.zeros(len(df))
    for k in range(N_FOLDS):
        tr, te = folds != k, folds == k
        m = lgb.LGBMClassifier(**_params())
        m.fit(df.loc[tr, cols], y[tr])
        raw_tr = m.predict_proba(df.loc[tr, cols])[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip").fit(raw_tr, y[tr])
        oof[te] = iso.predict(m.predict_proba(df.loc[te, cols])[:, 1])
        print(f"  fold {k}: trained on {tr.sum():,}, scored {te.sum():,}")

    out = df[["item_id", "txn_id", "customer_id", "family", "is_fake"]].copy()
    out["tamper_score_oof"] = oof
    out.to_parquet(DATA / "evidence_scored.parquet", index=False)

    print("\nmean out-of-fold tamper score by family:")
    print(out.groupby("family")["tamper_score_oof"].mean().round(3).to_string())
    print(f"\nsaved -> {DATA / 'evidence_scored.parquet'}")


if __name__ == "__main__":
    main()
