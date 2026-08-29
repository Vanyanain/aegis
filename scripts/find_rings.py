"""Detect abuse rings in the real transaction ledger.

Run: python -m scripts.find_rings
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aegis.rings import linkage as L  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA, DOCS = ROOT / "data", ROOT / "docs"


def main() -> None:
    led = pd.read_parquet(DATA / "real_ledger.parquet",
                          columns=["txn_id", "customer_id", "device_fingerprint",
                                   "card_product", "amount_inr", "disputed"])
    print(f"{len(led):,} real transactions")
    rings, lookup = L.build_rings(led)
    s = L.summarise(rings, led, lookup)

    print(f"\nrings found                 : {s['n_rings']:,}")
    print(f"entities inside rings       : {s['n_entities_in_rings']:,}")
    print(f"transactions inside rings   : {s['n_transactions_in_rings']:,}")
    print(f"\nchargeback rate INSIDE rings : {s['chargeback_rate_in_rings']:.2%}")
    print(f"chargeback rate outside      : {s['chargeback_rate_outside']:.2%}")
    print(f"lift                         : {s['lift']:.2f}x")
    print(f"chargeback value in rings    : Rs {s['chargeback_value_in_rings_inr']:,.0f}")
    print("\nlargest rings by chargeback value:")
    for r in s["largest_rings"][:6]:
        print(f"  ring {r['ring_id']:>3}  entities={r['n_entities']:>3}  devices={r['n_devices']:>3}  "
              f"txns={r['n_transactions']:>5}  chargebacks={r['n_chargebacks']:>4} "
              f"({r['chargeback_rate']:.0%})  Rs {r['chargeback_value_inr']:>12,.0f}")

    lookup.to_parquet(DATA / "real_rings.parquet", index=False)
    (DOCS / "rings.json").write_text(json.dumps(s, indent=2))
    print(f"\nsaved -> {DATA / 'real_rings.parquet'}, {DOCS / 'rings.json'}")


if __name__ == "__main__":
    main()
