"""Build the Side B evidence corpus from REAL receipt photographs.

Run: python -m scripts.make_real_evidence [n]

Replaces the rendered corpus entirely. Base documents are 1,973 real photographs:
CORD (Naver Clova, 1,000 Indonesian retail receipts with word-level annotations) and
SROIE (ICDAR 2019 Robust Reading Challenge, 973 scanned receipts). Real cameras, real
thermal print, real skew and lighting, real JPEG histories, real OCR difficulty.

Only the manipulation is programmatic, which is the same methodology as the benchmarks this
problem is measured against -- DocTamper (CVPR 2023) applies synthetic manipulations to
photographed documents, and AIForge-Doc (2026) builds its forgeries on CORD, SROIE and
WildReceipt.

Each item is bound to a real IEEE-CIS transaction so the ledger cross-check has a real
counterpart. Consistency by family is deliberate, so no single feature solves the task:

    genuine     photo untouched, amount matches the transaction        consistent
    copy_move   digits transplanted within the same receipt            MISMATCH (total changed)
    splice      region from a different real receipt pasted in         MISMATCH
    recycled    untouched photo, submitted against another order       MISMATCH (different order)

`recycled` remains the class that pixel forensics cannot reach and only the ledger can.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datasets import load_from_disk  # noqa: E402

from synth.receipts import tamper_real as TR  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
IMG = DATA / "evidence_real"
SEED = 20260822

FAMILY_MIX = {"genuine": 0.50, "copy_move": 0.22, "splice": 0.16, "recycled": 0.12}


def load_real_receipts(limit: int | None = None) -> list[TR.RealReceipt]:
    out: list[TR.RealReceipt] = []
    cord = load_from_disk(str(DATA / "real" / "cord"))
    for split in cord:
        for ex in cord[split]:
            try:
                out.append(TR.parse_cord(ex))
            except Exception:
                continue
            if limit and len(out) >= limit:
                return out
    try:
        sroie = load_from_disk(str(DATA / "real" / "sroie"))
        for split in sroie:
            for ex in sroie[split]:
                try:
                    out.append(TR.parse_sroie(ex))
                except Exception:
                    continue
                if limit and len(out) >= limit:
                    return out
    except Exception as e:
        print(f"  (sroie unavailable: {type(e).__name__})")
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
    IMG.mkdir(parents=True, exist_ok=True)
    for f in IMG.glob("*.jpg"):
        f.unlink()

    rng = random.Random(SEED)
    print("loading real receipt photographs ...", flush=True)
    receipts = load_real_receipts()
    print(f"  {len(receipts):,} real receipts "
          f"({sum(r.source == 'cord' for r in receipts)} CORD, "
          f"{sum(r.source == 'sroie' for r in receipts)} SROIE)", flush=True)
    if not receipts:
        raise SystemExit("no real receipts found - run the dataset download first")

    ledger = pd.read_parquet(
        DATA / "real_ledger.parquet",
        columns=["txn_id", "customer_id", "amount_inr", "ts", "descriptor_text", "disputed"],
    )
    # Prefer disputed transactions: evidence is submitted in support of a claim.
    disp = ledger[ledger["disputed"]]
    pool = pd.concat([disp, ledger.sample(n=min(len(ledger), n), random_state=SEED)])
    txns = pool.sample(n=n, replace=len(pool) < n, random_state=SEED).to_dict("records")

    fams = list(FAMILY_MIX)
    weights = [FAMILY_MIX[f] for f in fams]

    rows = []
    print(f"building {n:,} evidence items ...", flush=True)
    for k in range(n):
        fam = rng.choices(fams, weights=weights, k=1)[0]
        base = receipts[rng.randrange(len(receipts))]
        txn = txns[k]
        out = IMG / f"RV{k:06d}.jpg"

        if fam == "genuine":
            img = base.image
            TR.save(img, out, rng, second_pass=False)
            # A genuine receipt corroborates its own transaction: the claim is set to what
            # the document actually says, so the cross-check agrees.
            claimed = float(base.total) if base.total else float(txn["amount_inr"])
        elif fam == "copy_move":
            img = TR.make_copy_move(base, rng)
            TR.save(img, out, rng, second_pass=True)
            claimed = float(base.total) if base.total else float(txn["amount_inr"])
        elif fam == "splice":
            donor = receipts[rng.randrange(len(receipts))]
            img = TR.make_splice(base, donor, rng)
            TR.save(img, out, rng, second_pass=True)
            claimed = float(base.total) if base.total else float(txn["amount_inr"])
        else:  # recycled -- authentic document, wrong order
            img = base.image
            TR.save(img, out, rng, second_pass=False)
            other = txns[(k * 7919 + 13) % len(txns)]
            claimed = float(other["amount_inr"])

        truth = TR.arithmetic_truth(base)
        rows.append({
            "item_id": f"RV{k:06d}",
            "path": str(out.relative_to(ROOT)),
            "family": fam,
            "is_fake": int(fam != "genuine"),
            "source": base.source,
            "txn_id": txn["txn_id"],
            "customer_id": txn["customer_id"],
            "claimed_amount_inr": claimed,
            "claimed_ts": pd.Timestamp(txn["ts"]).isoformat(),
            "claimed_descriptor": txn["descriptor_text"],
            "descriptor_is_clear": True,
            "receipt_total": truth["total"],
            "gt_components_vs_total_rel": truth["components_vs_total_rel"],
            "gt_n_items": truth["n_items"],
        })
        if (k + 1) % 400 == 0:
            print(f"  {k + 1:,}/{n:,}", flush=True)

    man = pd.DataFrame(rows)
    man.to_parquet(DATA / "evidence_real_manifest.parquet", index=False)

    print("\n--- corpus ---")
    for fam, g in man.groupby("family"):
        print(f"  {fam:12s} n={len(g):5,}  sources={dict(g['source'].value_counts())}")
    mb = sum(f.stat().st_size for f in IMG.glob("*.jpg")) / 1e6
    print(f"\n  {len(man):,} images, {mb:.0f} MB -> {IMG}")

    (DATA / "evidence_real_card.json").write_text(json.dumps({
        "n": n, "seed": SEED, "family_mix": FAMILY_MIX,
        "base_documents": "CORD (naver-clova-ix/cord-v2) + SROIE (ICDAR 2019 RRC)",
        "methodology": (
            "Real photographs; manipulations applied programmatically to real annotated "
            "regions. Same methodology as DocTamper (CVPR 2023) and AIForge-Doc (2026)."
        ),
        "disclosure": (
            "Manipulated images are detection targets built offline for detector training "
            "and evaluation. They are never produced or exposed at serving time."
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
