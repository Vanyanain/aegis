"""Forensic feature extraction over the REAL receipt evidence corpus.

Run: python -m scripts.extract_evidence_real
Output: data/evidence_real_features.parquet

Same extractor as the synthetic corpus (aegis/sideb/forensics.py) pointed at real
photographs. Real receipts are larger and noisier than rendered ones, so OCR is slower and
less reliable -- which is the point: the arithmetic layer's real-world accuracy is now
measurable rather than assumed.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aegis.sideb import forensics as FX  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _one(rec: dict) -> dict:
    try:
        f = FX.extract(
            ROOT / rec["path"], rec["claimed_amount_inr"], rec["claimed_ts"],
            rec["claimed_descriptor"],
        )
    except Exception as e:
        f = {"extract_error": 1.0, "error": f"{type(e).__name__}: {str(e)[:60]}"}
    f["item_id"] = rec["item_id"]
    return f


def main() -> None:
    man = pd.read_parquet(DATA / "evidence_real_manifest.parquet")
    recs = man.to_dict("records")
    t0 = time.time()
    print(f"extracting forensic features for {len(recs):,} REAL receipt items ...", flush=True)

    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(_one, recs, chunksize=8))

    feats = pd.DataFrame(rows)
    out = man.merge(feats, on="item_id", how="left")
    out.to_parquet(DATA / "evidence_real_features.parquet", index=False)

    n_err = int(out.get("extract_error", pd.Series(0, index=out.index)).fillna(0).sum())
    dt = time.time() - t0
    print(f"  {len(out):,} rows, {n_err} extraction errors, {dt:.0f}s "
          f"({dt / len(recs) * 1000:.0f} ms/image)")

    # How well does the arithmetic layer actually work on real documents? The manifest
    # carries CORD's annotated ground truth, so this is directly measurable.
    gt = out.dropna(subset=["gt_components_vs_total_rel"])
    if len(gt):
        gen = gt[gt["family"] == "genuine"]
        print(f"\n  genuine real receipts: {len(gen):,}")
        print(f"    annotation says reconciles (<1%) : {(gen['gt_components_vs_total_rel'] < 0.01).mean():.1%}")
        if "arith_any_break" in gen:
            print(f"    OUR arithmetic layer flags a break: {gen['arith_any_break'].mean():.1%}")
        if "arith_parsed" in gen:
            print(f"    mean fields parsed by OCR        : {gen['arith_parsed'].mean():.2f}")
    print(f"\nsaved -> {DATA / 'evidence_real_features.parquet'}")


if __name__ == "__main__":
    main()
