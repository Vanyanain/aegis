"""Side B on REAL receipt photographs.

Run: python -m scripts.train_real_sideb

Base documents are real (CORD + SROIE); the manipulations are programmatic, matching the
methodology of DocTamper (CVPR 2023) and AIForge-Doc (2026).

What changes versus the rendered corpus is the DOMINANT SIGNAL. Rendered fakes carried
broken arithmetic, so the arithmetic layer led. Here the fakes are pixel manipulations of
genuine documents whose numbers were never recomputed, so compression and noise forensics
have to carry the result -- and OCR on a real photograph is far less reliable than on a
clean render. Both effects are visible in the feature-group importances below and neither
is smoothed over.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import joblib, lightgbm as lgb
from sklearn.isotonic import IsotonicRegression
from aegis.sideb import forensics as FX
from scripts.train_sideb import report, HUMAN_BASELINE

ROOT = Path(__file__).resolve().parents[1]
DATA, MODELS, DOCS = ROOT / "data", ROOT / "models", ROOT / "docs"
SEED = 20260822

NON_FEATURES = {
    "item_id", "path", "family", "is_fake", "txn_id", "customer_id", "source",
    "claimed_amount_inr", "claimed_ts", "claimed_descriptor", "descriptor_is_clear",
    "receipt_total", "gt_components_vs_total_rel", "gt_n_items", "error", "extract_error",
}
PARAMS = dict(n_estimators=900, learning_rate=0.03, num_leaves=24, max_depth=6,
              min_child_samples=25, subsample=0.85, subsample_freq=1,
              colsample_bytree=0.75, reg_lambda=2.0, random_state=SEED, verbose=-1)


def cols(df, drop=frozenset()):
    return [c for c in df.columns
            if c not in NON_FEATURES and pd.api.types.is_numeric_dtype(df[c])
            and FX.group_of(c) not in drop]


def split(df, seed=SEED):
    rng = np.random.default_rng(seed)
    cu = np.asarray(df["customer_id"].unique(), dtype=object); rng.shuffle(cu)
    n = len(cu); tr, ca, te = cu[:int(.6*n)], cu[int(.6*n):int(.8*n)], cu[int(.8*n):]
    m = df["customer_id"].to_numpy()
    return np.isin(m, tr), np.isin(m, ca), np.isin(m, te)


def fit(df, c, masks):
    tr, ca, te = masks; y = df["is_fake"].to_numpy(); X = df[c]
    m = lgb.LGBMClassifier(**PARAMS)
    m.fit(X[tr], y[tr], eval_set=[(X[ca], y[ca])], eval_metric="average_precision",
          callbacks=[lgb.early_stopping(80, verbose=False)])
    iso = IsotonicRegression(out_of_bounds="clip").fit(m.predict_proba(X[ca])[:,1], y[ca])
    return m, iso, y[te], iso.predict(m.predict_proba(X[te])[:,1])


def main():
    df = pd.read_parquet(DATA / "evidence_real_features.parquet")
    if "extract_error" in df: df = df[df["extract_error"].isna()]
    df = df.fillna({c: 0.0 for c in df.columns if pd.api.types.is_numeric_dtype(df[c])})
    c = cols(df); masks = split(df)
    print(f"{len(df):,} REAL receipt items, {len(c)} features")
    print(f"  train {masks[0].sum():,} calib {masks[1].sum():,} test {masks[2].sum():,}")
    print(f"  sources: {dict(df.source.value_counts())}")

    model, iso, y, p = fit(df, c, masks)
    ho = report(y, p)
    print(f"\n[held-out on REAL photos]  P {ho['precision']:.3f}  R {ho['recall']:.3f}"
          f"  F1 {ho['f1']:.3f}  PR-AUC {ho['pr_auc']:.3f}  FPR {ho['fpr']:.3f}")
    print(f"  human baseline: R {HUMAN_BASELINE['recall']:.3f}  FPR {HUMAN_BASELINE['fpr']:.3f}")

    te = df[masks[2]].copy(); te["p"] = p
    per_family = {}
    for fam, g in te.groupby("family"):
        k = "false_positive_rate" if fam == "genuine" else "recall"
        per_family[fam] = {"n": int(len(g)), k: float((g["p"] >= .5).mean())}
    print("\n  per family:")
    for fam, r in per_family.items():
        kk = "recall" if "recall" in r else "false_positive_rate"
        print(f"    {fam:12s} n={r['n']:4d}  {kk}={r[kk]:.3f}")

    lofo = {}
    print("\n[leave-one-family-out]")
    for held in ("copy_move", "splice", "recycled"):
        trn = df[(df.family != held) & (masks[0] | masks[1])]
        ev = pd.concat([df[df.family == held], df[masks[2] & (df.family == "genuine")]])
        if trn.is_fake.nunique() < 2 or ev.is_fake.nunique() < 2: continue
        m = lgb.LGBMClassifier(**PARAMS); m.fit(trn[c], trn.is_fake.to_numpy())
        r = report(ev.is_fake.to_numpy(), m.predict_proba(ev[c])[:,1])
        lofo[held] = r
        print(f"    unseen={held:11s} recall {r['recall']:.3f}  FPR {r['fpr']:.3f}  PR-AUC {r['pr_auc']:.3f}")

    abl = {}
    print("\n[ablations]")
    for name, drop in [("full", set()), ("no_crosscheck", {"crosscheck"}),
                       ("no_compression", {"compression"}), ("no_arithmetic", {"arithmetic"}),
                       ("no_provenance", {"provenance"})]:
        cc = cols(df, drop)
        _, _, yy, pp = fit(df, cc, masks); r = report(yy, pp)
        abl[name] = {"n_features": len(cc), **r}
        print(f"    {name:16s} {len(cc):3d} feats  R {r['recall']:.3f}  P {r['precision']:.3f}  PR-AUC {r['pr_auc']:.3f}")

    imp = pd.Series(model.feature_importances_, index=c).sort_values(ascending=False)
    by_group = {}
    for k, v in imp.items(): by_group[FX.group_of(k)] = by_group.get(FX.group_of(k), 0) + int(v)
    print("\n  importance by group:")
    for g, v in sorted(by_group.items(), key=lambda kv: -kv[1]): print(f"    {g:14s} {v:6d}")
    print("\n  top features:")
    for k, v in imp.head(10).items(): print(f"    {k:32s} {int(v)}")

    out = {"held_out": ho, "per_family": per_family, "leave_one_family_out": lofo,
           "ablations": abl, "human_baseline": HUMAN_BASELINE,
           "beats_human_recall": bool(ho["recall"] > HUMAN_BASELINE["recall"]),
           "beats_human_fpr": bool(ho["fpr"] < HUMAN_BASELINE["fpr"]),
           "importance_by_group": by_group, "n_features": len(c),
           "corpus": "CORD + SROIE real photographs; programmatic manipulation"}
    joblib.dump({"model": model, "calibrator": iso, "features": c, "metrics": out,
                 "seed": SEED}, MODELS / "real_sideb_forensics.joblib")
    (DOCS / "metrics_real_sideb.json").write_text(json.dumps(out, indent=2))
    te[["item_id","family","is_fake","p","txn_id","customer_id"]].to_parquet(
        DATA / "real_sideb_test_scored.parquet", index=False)
    print(f"\nsaved -> {MODELS / 'real_sideb_forensics.joblib'}")


if __name__ == "__main__":
    main()
