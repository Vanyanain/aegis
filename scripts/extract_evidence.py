"""Run forensic extraction over the whole evidence corpus.

Run: python -m scripts.extract_evidence
Output: data/evidence_features.parquet

OCR dominates the cost (~0.5s/image), so this is parallelised across processes and cached
to parquet. Training and every ablation then reads the cached features rather than
re-running Tesseract.
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
    except Exception as e:  # a corrupt file must not kill the whole run
        f = {"extract_error": 1.0, "error": str(e)[:80]}
    f["item_id"] = rec["item_id"]
    return f


def main() -> None:
    man = pd.read_parquet(DATA / "evidence_manifest.parquet")
    recs = man.to_dict("records")
    t0 = time.time()
    print(f"extracting forensic features for {len(recs):,} items ...")

    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(_one, recs, chunksize=16))

    feats = pd.DataFrame(rows)
    out = man.merge(feats, on="item_id", how="left")
    out.to_parquet(DATA / "evidence_features.parquet", index=False)

    n_err = int(out.get("extract_error", pd.Series(0, index=out.index)).fillna(0).sum())
    dt = time.time() - t0
    print(f"  {len(out):,} rows, {n_err} extraction errors, {dt:.0f}s "
          f"({dt / len(recs) * 1000:.0f} ms/image)")

    cols = [c for c in out.columns if FX.group_of(c) != "other"]
    print(f"  {len(cols)} forensic features across {len(FX.GROUPS)} groups")
    print(f"saved -> {DATA / 'evidence_features.parquet'}")


if __name__ == "__main__":
    main()
