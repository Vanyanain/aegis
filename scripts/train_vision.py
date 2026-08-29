"""Train the noise-residual tamper CNN on REAL receipt photographs.

Run: python -m scripts.train_vision

Runs in its own process and NEVER imports LightGBM: this environment's torch and LightGBM
link different OpenMP runtimes and mixing them segfaults. The trained network is exported
to ONNX so serving can score it without torch present.

The split is by customer and uses the SAME seed as the classical Side B model, so the two
are evaluated on identical held-out items and their scores can be honestly ensembled.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import torch, torch.nn as nn
from aegis.sideb import vision as V

ROOT = Path(__file__).resolve().parents[1]
DATA, MODELS, DOCS = ROOT/"data", ROOT/"models", ROOT/"docs"
SEED = 20260822
EPOCHS, BATCH = 14, 16


def split(df, seed=SEED):
    rng = np.random.default_rng(seed)
    cu = np.asarray(df["customer_id"].unique(), dtype=object); rng.shuffle(cu)
    n=len(cu); tr,ca,te = cu[:int(.6*n)], cu[int(.6*n):int(.8*n)], cu[int(.8*n):]
    m = df["customer_id"].to_numpy()
    return np.isin(m,tr), np.isin(m,ca), np.isin(m,te)


def load_x(paths):
    X = np.zeros((len(paths), 2, V.INPUT_SIZE, V.INPUT_SIZE), dtype=np.float32)
    for i, p in enumerate(paths):
        try:
            X[i] = V.prepare(Image.open(ROOT/p))
        except Exception:
            pass
        if (i+1) % 500 == 0: print(f"    prepared {i+1}/{len(paths)}", flush=True)
    return X


def main():
    df = pd.read_parquet(DATA/"evidence_real_manifest.parquet")
    tr, ca, te = split(df)
    print(f"{len(df):,} real items | train {tr.sum()} calib {ca.sum()} test {te.sum()}")
    t0=time.time(); print("preparing inputs (ELA + grayscale) ...", flush=True)
    X = load_x(df["path"].tolist()); y = df["is_fake"].to_numpy().astype(np.float32)
    print(f"  done in {time.time()-t0:.0f}s")

    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = V.build_model().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=1.2e-3, weight_decay=1e-4)
    # Positives are ~50% here, but keep the weighting explicit so the mix can change.
    pos_w = torch.tensor(float((y[tr]==0).sum()/max((y[tr]==1).sum(),1)), device=dev)
    lossf = nn.BCEWithLogitsLoss(pos_weight=pos_w)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)

    Xtr, ytr = torch.tensor(X[tr]), torch.tensor(y[tr])
    Xca, yca = torch.tensor(X[ca]).to(dev), y[ca]
    Xte = torch.tensor(X[te]).to(dev); yte = y[te]

    def predict(xt):
        model.eval(); out=[]
        with torch.no_grad():
            for i in range(0, len(xt), 32):
                out.append(torch.sigmoid(model(xt[i:i+32])).cpu().numpy())
        return np.concatenate(out)

    from sklearn.metrics import average_precision_score, roc_auc_score
    best, best_state = -1.0, None
    for ep in range(EPOCHS):
        model.train(); perm = torch.randperm(len(Xtr)); tot=0.0
        for i in range(0, len(perm), BATCH):
            idx = perm[i:i+BATCH]
            xb, yb = Xtr[idx].to(dev), ytr[idx].to(dev)
            # Flips only: rotation or colour jitter would alter the noise statistics that
            # are the entire signal here.
            if np.random.rand() < 0.5: xb = torch.flip(xb, dims=[3])
            opt.zero_grad(); loss = lossf(model(xb), yb); loss.backward(); opt.step()
            tot += float(loss)*len(idx)
        sched.step()
        pv = predict(Xca); ap = average_precision_score(yca, pv)
        print(f"  epoch {ep+1:2d}/{EPOCHS} loss {tot/len(perm):.4f}  calib PR-AUC {ap:.4f}", flush=True)
        if ap > best:
            best, best_state = ap, {k: v.detach().cpu().clone() for k,v in model.state_dict().items()}

    model.load_state_dict(best_state)
    p = predict(Xte)
    ap, auc = average_precision_score(yte, p), roc_auc_score(yte, p)
    pred = (p>=0.5).astype(int)
    tp = int(((pred==1)&(yte==1)).sum()); fp=int(((pred==1)&(yte==0)).sum())
    fn = int(((pred==0)&(yte==1)).sum()); tn=int(((pred==0)&(yte==0)).sum())
    prec = tp/max(tp+fp,1); rec = tp/max(tp+fn,1)
    print(f"\n[VISION on real photos]  P {prec:.3f}  R {rec:.3f}  PR-AUC {ap:.3f}  ROC-AUC {auc:.3f}  FPR {fp/max(fp+tn,1):.3f}")

    tedf = df[te].copy(); tedf["vision_p"] = p
    print("\n  per family:")
    for fam, g in tedf.groupby("family"):
        k = "FPR" if fam=="genuine" else "recall"
        print(f"    {fam:12s} n={len(g):4d}  {k}={(g.vision_p>=0.5).mean():.3f}")

    MODELS.mkdir(exist_ok=True)
    model.eval().cpu()
    torch.onnx.export(model, torch.zeros(1,2,V.INPUT_SIZE,V.INPUT_SIZE),
                      str(MODELS/"tampernet.onnx"), input_names=["input"],
                      output_names=["logit"], opset_version=17,
                      dynamic_axes={"input":{0:"n"},"logit":{0:"n"}})
    tedf[["item_id","family","is_fake","vision_p","customer_id"]].to_parquet(
        DATA/"real_vision_test_scored.parquet", index=False)
    (DOCS/"metrics_vision.json").write_text(json.dumps({
        "precision":prec,"recall":rec,"pr_auc":float(ap),"roc_auc":float(auc),
        "fpr":fp/max(fp+tn,1),"confusion":{"tn":tn,"fp":fp,"fn":fn,"tp":tp},
        "n_test":int(te.sum()),"epochs":EPOCHS,"input_size":V.INPUT_SIZE,
        "per_family":{f:float((g.vision_p>=0.5).mean()) for f,g in tedf.groupby("family")},
        "architecture":"fixed SRM high-pass front end + 4 conv blocks + global max pool",
    }, indent=2))
    print(f"\nsaved -> {MODELS/'tampernet.onnx'}")


if __name__ == "__main__":
    main()
