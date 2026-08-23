"""Build the evidence corpus: genuine receipts and four fake families.

Run: python -m scripts.make_receipts [n]

Each receipt is bound to a real transaction from the ledger, because Side B's whole thesis
is that document forensics and the transaction record answer different questions and only
together cover the space. The binding is what makes the `recycled` family detectable at all.

Cross-check consistency by family -- this layout is deliberate, and it is the reason no
single feature can solve the problem:

    genuine         receipt agrees with the transaction        consistent
    ai_generated    forger matches the amount they're claiming  consistent
    template_forge  generator computes a matching total         consistent
    digital_edit    total inflated above the real charge        MISMATCH
    recycled        different order entirely                     MISMATCH

So arithmetic and typography must carry ai_generated and template_forge; ELA and
double-compression must carry digital_edit; and the ledger cross-check is the ONLY thing
that can carry recycled.
"""

from __future__ import annotations

import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from synth.receipts import fakes  # noqa: E402
from synth.ledger import CLEAR_DESCRIPTORS  # noqa: E402
from synth.receipts.render import fit_to_amount, make_spec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
IMG_DIR = DATA / "evidence"
SEED = 20260822

# Genuine is half the corpus; the rest splits across the four fake families. `recycled` is
# given real weight despite being the hardest to construct, because it is the class that
# justifies the product's architecture.
FAMILY_MIX = {
    "genuine": 0.50,
    "ai_generated": 0.16,
    "digital_edit": 0.14,
    "template_forge": 0.11,
    "recycled": 0.09,
}

# Who fabricates evidence is NOT independent of why they are disputing, and treating it as
# independent makes the intent model blind to the strongest signal it could have. A
# cardholder whose card was genuinely stolen has no reason to forge a receipt -- they are
# the victim, and the transaction is not theirs to document. A customer whose parcel really
# never arrived has a real grievance and usually real proof. It is first-party misuse that
# needs a document to exist and does not have one.
#
# So the family is drawn conditional on the dispute's latent intent. This is what lets the
# fusion layer in M3 learn that a tampered document is evidence AGAINST criminal fraud,
# rather than scoring 67% criminal_fraud on a case with a doctored receipt.
FAMILY_MIX_BY_INTENT = {
    "first_party_misuse": {
        "genuine": 0.28, "ai_generated": 0.26, "digital_edit": 0.20,
        "template_forge": 0.16, "recycled": 0.10,
    },
    "genuine_service_failure": {
        "genuine": 0.86, "ai_generated": 0.05, "digital_edit": 0.04,
        "template_forge": 0.03, "recycled": 0.02,
    },
    "criminal_fraud": {
        "genuine": 0.80, "ai_generated": 0.08, "digital_edit": 0.05,
        "template_forge": 0.04, "recycled": 0.03,
    },
}


def _one(args) -> dict:
    # Everything a worker needs arrives through this tuple. macOS starts worker processes
    # with "spawn", not "fork", so module-level globals set in main() are NOT inherited --
    # they re-import as empty and silently produce a column of nulls.
    idx, family, seed, txn, latent_intent = args
    rng = random.Random(seed)
    out = IMG_DIR / f"EV{idx:06d}.jpg"
    claimed_amount = float(txn["amount_inr"])
    claimed_ts = pd.Timestamp(txn["ts"]).to_pydatetime()
    # The merchant that actually charged the card, derived from the transaction category.
    merchant_name = CLEAR_DESCRIPTORS.get(txn["category"])

    if family == "genuine":
        spec = make_spec(rng, ts=claimed_ts, merchant_name=merchant_name)
        fit_to_amount(spec, claimed_amount)
        fakes.make_genuine(rng, out, spec=spec)

    elif family == "ai_generated":
        # Total pinned to the claim; the inconsistency lives inside the document.
        spec = fakes.make_ai_generated(rng, out, target=claimed_amount, ts=claimed_ts,
               merchant_name=merchant_name)

    elif family == "template_forge":
        # Total pinned to the claim AND arithmetically correct: forensics-only family.
        spec = fakes.make_template_forge(rng, out, target=claimed_amount, ts=claimed_ts,
               merchant_name=merchant_name)

    elif family == "digital_edit":
        # Starts at the real charge, then the printed total is inflated above it.
        spec = fakes.make_digital_edit(rng, out, target=claimed_amount, ts=claimed_ts,
                                       merchant_name=merchant_name)

    else:  # recycled
        # A real receipt from a genuinely different order: different date, different
        # amount, often a different store. Nothing about the image is fake.
        spec = fakes.make_recycled(rng, out, ts_offset_days=rng.randint(-400, -20))

    return {
        "item_id": f"EV{idx:06d}",
        "path": str(out.relative_to(ROOT)),
        "family": family,
        "is_fake": int(family != "genuine"),
        "txn_id": txn["txn_id"],
        "customer_id": txn["customer_id"],
        # What the transaction record says.
        "claimed_amount_inr": claimed_amount,
        "claimed_ts": claimed_ts.isoformat(),
        "claimed_descriptor": txn["descriptor_text"],
        "descriptor_is_clear": bool(txn["descriptor_is_clear"]),
        "latent_intent": latent_intent,
        # What the receipt actually shows. Ground truth for evaluation only -- the
        # forensic pipeline must recover these from the image via OCR.
        "receipt_total": float(spec.total),
        "receipt_ts": spec.ts.isoformat(),
        "receipt_merchant": spec.merchant,
        "delivery": spec.delivery,
        "arithmetic_broken": bool(spec.arithmetic_broken),
        "broken_fields": ",".join(spec.broken_fields),
    }


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for f in IMG_DIR.glob("*.jpg"):
        f.unlink()

    ledger = pd.read_parquet(DATA / "ledger.parquet")
    rng = random.Random(SEED)

    # Prefer transactions that actually attracted a dispute -- evidence is submitted in
    # support of a claim, so the corpus should look like the cases the console will see.
    disputed = ledger[ledger["disputed"]]
    pool = pd.concat([disputed, ledger.sample(n=min(len(ledger), n), random_state=SEED)])
    pool = pool.sample(n=n, replace=len(pool) < n, random_state=SEED)
    txns = pool[["txn_id", "customer_id", "amount_inr", "ts", "descriptor_text",
                 "category", "descriptor_is_clear"]].to_dict("records")

    # Where the transaction attracted a dispute, draw the family conditional on that
    # dispute's latent intent; otherwise fall back to the global mix.
    cases = pd.read_parquet(DATA / "cases.parquet")
    intent_by_txn = dict(zip(cases["txn_id"], cases["latent_intent"]))

    families: list[str] = []
    pool = list(FAMILY_MIX.keys())
    base_w = [FAMILY_MIX[f] for f in pool]
    for t in txns:
        intent = intent_by_txn.get(t["txn_id"])
        mix = FAMILY_MIX_BY_INTENT.get(intent)
        w = [mix[f] for f in pool] if mix else base_w
        families.append(rng.choices(pool, weights=w, k=1)[0])

    args = [
        (i, families[i], SEED + i * 7919, txns[i], intent_by_txn.get(txns[i]["txn_id"]))
        for i in range(n)
    ]

    print(f"rendering {n:,} receipts across {len(FAMILY_MIX)} families ...")
    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(_one, args, chunksize=24))

    man = pd.DataFrame(rows)
    man.to_parquet(DATA / "evidence_manifest.parquet", index=False)

    print("\n--- corpus ---")
    for fam, g in man.groupby("family"):
        mismatch = (g["receipt_total"] - g["claimed_amount_inr"]).abs() > 1.0
        print(f"  {fam:16s} n={len(g):5,}  ledger-mismatch={mismatch.mean():5.1%}  "
              f"arithmetic-broken={g['arithmetic_broken'].mean():5.1%}")
    size_mb = sum(f.stat().st_size for f in IMG_DIR.glob('*.jpg')) / 1e6
    print(f"\n  {len(man):,} images, {size_mb:.0f} MB -> {IMG_DIR}")

    (DATA / "evidence_card.json").write_text(json.dumps({
        "n": n, "seed": SEED, "family_mix": FAMILY_MIX,
        "disclosure": "Fully synthetic. Fake families are detection targets generated "
                      "offline for detector training and evaluation only; they are never "
                      "produced or exposed at serving time.",
    }, indent=2))


if __name__ == "__main__":
    main()
