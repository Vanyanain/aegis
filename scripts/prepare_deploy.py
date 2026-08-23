"""Stage a slim evidence subset for the container image.

Run: python -m scripts.prepare_deploy

The training corpus is ~123 MB of receipt images. None of it belongs in a serving image:
the model ships fitted, and the console only ever renders evidence for disputes that appear
in the queue. This copies just those files into data/evidence_demo/, which the Dockerfile
mounts as data/evidence/.

Selection is deliberate rather than "first N": the queue is ranked by expected recovery, so
the top of it is what a reviewer will actually click, and every fake family plus genuine is
guaranteed representation so the demo can show each verdict path.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "evidence_demo"

# Enough to cover the visible queue with headroom, small enough to keep the image lean.
TOP_N_DISPUTES = 400
PER_FAMILY_FLOOR = 25


def main() -> None:
    man = pd.read_parquet(DATA / "evidence_manifest.parquet")
    cases = pd.read_parquet(DATA / "cases.parquet")
    scored = pd.read_parquet(DATA / "sidea_test_scored.parquet")

    # Rank disputes the way the console does, so the staged files match what is reachable.
    rank = cases.merge(
        scored[["dispute_id", "win_prob"]], on="dispute_id", how="left"
    )
    rank["win_prob"] = rank["win_prob"].fillna(0.3)
    rank["expected"] = rank["win_prob"] * rank["amount_inr"]
    top_txns = set(rank.sort_values("expected", ascending=False).head(TOP_N_DISPUTES)["txn_id"])

    keep = man[man["txn_id"].isin(top_txns)].copy()

    # Guarantee every family is present even if the ranking happens to miss one, otherwise a
    # demo could have no example of the class the whole architecture exists to catch.
    for fam, g in man.groupby("family"):
        have = (keep["family"] == fam).sum()
        if have < PER_FAMILY_FLOOR:
            extra = g[~g["item_id"].isin(keep["item_id"])].head(PER_FAMILY_FLOOR - have)
            keep = pd.concat([keep, extra])
    keep = keep.drop_duplicates("item_id")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    copied = 0
    for p in keep["path"]:
        src = ROOT / p
        if src.exists():
            shutil.copy2(src, OUT / src.name)
            copied += 1

    size_mb = sum(f.stat().st_size for f in OUT.glob("*.jpg")) / 1e6
    print(f"staged {copied:,} evidence images ({size_mb:.1f} MB) -> {OUT}")
    print("\nby family:")
    print(keep["family"].value_counts().to_string())

    parquet_mb = sum(f.stat().st_size for f in DATA.glob("*.parquet")) / 1e6
    model_mb = sum(f.stat().st_size for f in (ROOT / "models").glob("*.joblib")) / 1e6
    print(f"\nimage payload: {size_mb + parquet_mb + model_mb:.1f} MB "
          f"(evidence {size_mb:.1f} + data {parquet_mb:.1f} + models {model_mb:.1f})")


if __name__ == "__main__":
    main()
