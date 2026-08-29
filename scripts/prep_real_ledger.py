"""Build the AEGIS ledger from REAL transaction data (IEEE-CIS Fraud Detection).

Run: python -m scripts.prep_real_ledger

WHY THIS REPLACED THE SYNTHETIC LEDGER.

The original build synthesised its ledger on the stated grounds that no public dataset
carries the linked device / email / address history CE 3.0 is matched over. That was wrong.
IEEE-CIS (590,540 real transactions, 394 columns, Vesta Corporation) carries all of it, and
its `isFraud` label is defined by the data provider as a *reported chargeback on the card* —
so these are real disputes, not a proxy for them.

WHAT MAPS TO WHAT, AND WHAT DOES NOT.

    CE 3.0 element                 IEEE-CIS source
    ------------------------------ ----------------------------------------------------
    Device fingerprint  (MAIN)     DeviceInfo + id_30 (OS) + id_31 (browser)
                                   + id_32 (colour depth) + id_33 (screen resolution)
    Customer purchase IP (MAIN)    NOT AVAILABLE - see below
    Shipping address (SECONDARY)   addr1 + addr2
    Customer email  (SECONDARY)    P_emaildomain
    Customer account ID (SECONDARY) NOT AVAILABLE
    Payment credential             card1..card6

A device fingerprint genuinely IS the tuple above -- commercial fingerprinting libraries
hash exactly these components (user agent, OS, browser build, colour depth, screen
geometry, device model). Composing it here is reconstruction, not invention.

Purchase IP is a different matter. IEEE-CIS anonymises its network identifiers into
unlabelled `id_*` columns. Community folklore maps some of them to IP-derived quantities,
but Vesta never published that, so AEGIS does not pretend to have an IP. The consequence is
stated rather than hidden: on this dataset the **"two Main elements" qualification path is
not assessable**, and every qualifying case must go through "one Main + one Secondary".
Real-world qualification rates would therefore be *higher* than what we measure here.

THE 182-DAY PROBLEM.

CE 3.0 requires priors aged 120-364 days. The IEEE-CIS training period spans 182 days, so a
120-day lookback only exists for disputes raised on day 120 or later. Disputes before that
cannot qualify *by construction* and are excluded from the denominator rather than counted
as failures. The reported rate is still a lower bound: with a full year of history more
customers would have priors inside the window. `--min-age` runs the sensitivity analysis.

Entity resolution uses the standard construction from this dataset's literature:
`card1 + addr1 + (transaction_day - D1)`, where D1 is days since the card's first
appearance, so the offset is constant per client and recovers a stable customer identity.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "real"
OUT = ROOT / "data"

# The dataset's own epoch is arbitrary; anchoring it to a real date makes the rulebook's
# effective-date logic meaningful and the console legible.
EPOCH = pd.Timestamp("2025-01-01")

TXN_COLS = [
    "TransactionID", "TransactionDT", "TransactionAmt", "ProductCD", "isFraud",
    "card1", "card2", "card3", "card4", "card5", "card6",
    "addr1", "addr2", "dist1", "P_emaildomain", "R_emaildomain",
    "D1", "D2", "D3", "D4", "D10", "D15",
    "C1", "C2", "C5", "C13", "C14",
    "V95", "V96", "V97", "V126", "V127", "V128", "V307", "V308", "V310",
]
ID_COLS = [
    "TransactionID", "id_01", "id_02", "id_05", "id_06", "id_09", "id_11",
    "id_13", "id_17", "id_19", "id_20", "id_30", "id_31", "id_32", "id_33",
    "id_35", "id_36", "id_37", "id_38", "DeviceType", "DeviceInfo",
]


def _h(*parts) -> str | None:
    """Stable short hash of the supplied components, or None if all are missing."""
    vals = [str(p) for p in parts if p is not None and p == p and str(p) != "nan"]
    if not vals:
        return None
    return hashlib.sha1("|".join(vals).encode()).hexdigest()[:16]


def build(min_age: int = 120, max_age: int = 364) -> None:
    print("loading IEEE-CIS ...")
    t = pd.read_csv(RAW / "train_transaction.csv", usecols=TXN_COLS)
    i = pd.read_csv(RAW / "train_identity.csv", usecols=ID_COLS)
    df = t.merge(i, on="TransactionID", how="left")
    print(f"  {len(df):,} transactions, {df.isFraud.sum():,} reported chargebacks "
          f"({df.isFraud.mean():.2%})")

    df["day"] = (df["TransactionDT"] / 86400).astype(int)
    df["ts"] = EPOCH + pd.to_timedelta(df["TransactionDT"], unit="s")

    # --- customer entity ---------------------------------------------------------
    def s(col):
        return df[col].astype("object").where(df[col].notna(), "NA").astype(str)

    offset = (df["day"] - df["D1"].fillna(-999)).astype(int).astype(str)
    df["customer_id"] = "C_" + (s("card1") + "_" + s("addr1") + "_" + offset).map(
        lambda v: hashlib.sha1(v.encode()).hexdigest()[:14]
    )

    # The payment credential CE 3.0 requires priors to share.
    #
    # NOT card1..card6. Those are card ATTRIBUTES -- issuer, type, category, country -- and
    # they take only 14,893 distinct combinations across 590,540 transactions, roughly 40
    # transactions each. Using them as the "same credential" key would treat every customer
    # of the same issuer as one cardholder and would wildly overstate available history.
    # The resolved entity (card1 + addr1 + D1 offset) is the closest available proxy for an
    # individual credential, so the credential key and the customer key coincide here.
    df["card_token"] = "tok_" + df["customer_id"].str.slice(2)
    df["card_product"] = [
        _h(a, b, c, d, e, f)
        for a, b, c, d, e, f in zip(df.card1, df.card2, df.card3, df.card4, df.card5, df.card6)
    ]

    # --- CE 3.0 elements ---------------------------------------------------------
    # MAIN: device fingerprint, composed from the same components a real fingerprinting
    # library hashes. Present only where the merchant actually captured device data --
    # which on this dataset is a minority of transactions, and that IS the finding.
    df["device_fingerprint"] = [
        _h(a, b, c, d, e)
        for a, b, c, d, e in zip(df.DeviceInfo, df.id_30, df.id_31, df.id_32, df.id_33)
    ]
    # Same MAIN slot, coarser: device model alone, where the full fingerprint is unavailable.
    df["device_id"] = [_h(a, b) for a, b in zip(df.DeviceInfo, df.DeviceType)]

    # MAIN: purchase IP. Anonymised by the data provider; deliberately left empty rather
    # than guessed at from unlabelled id_* columns.
    df["purchase_ip"] = None

    # SECONDARY
    df["customer_email"] = df["P_emaildomain"]
    df["shipping_address"] = [_h(a, b) for a, b in zip(df.addr1, df.addr2)]
    df["customer_account_id"] = None  # no merchant-side account identifier in this dataset

    # --- fields the rulebook checks ----------------------------------------------
    df["amount_inr"] = df["TransactionAmt"] * 88.0  # USD-denominated source; FX is an input
    df["merchandise_or_services"] = np.where(
        df["ProductCD"].isin(["W", "H"]), "merchandise", "services"
    )
    df["product_description"] = "ProductCD " + df["ProductCD"].astype(str)
    df["descriptor_text"] = "VESTA*" + df["ProductCD"].astype(str)
    df["descriptor_is_clear"] = True
    df["status"] = "paid"
    df["is_validation_charge"] = False
    df["disputed"] = df["isFraud"].astype(bool)
    df["tc40_reported"] = df["isFraud"].astype(bool)
    df["channel"] = np.where(df["DeviceType"].eq("mobile"), "app",
                     np.where(df["DeviceType"].eq("desktop"), "web", "unknown"))
    df["txn_id"] = "T" + df["TransactionID"].astype(str)
    df["issuer"] = df["card4"].fillna("unknown").astype(str) + "/" + df["card6"].fillna("unknown").astype(str)

    print("\ncapture coverage of CE 3.0 elements (this is the product thesis, measured):")
    for c in ["device_fingerprint", "device_id", "purchase_ip", "customer_email",
              "shipping_address", "customer_account_id"]:
        cov = df[c].notna().mean() if df[c].notna().any() else 0.0
        note = "  <- anonymised in this dataset" if c in ("purchase_ip", "customer_account_id") else ""
        print(f"  {c:22s} {cov:6.1%}{note}")

    # --- disputes ----------------------------------------------------------------
    disp = df[df["isFraud"] == 1].copy()
    disp["assessable"] = disp["day"] >= min_age
    print(f"\nchargebacks: {len(disp):,}   assessable (day >= {min_age}): "
          f"{disp['assessable'].sum():,} ({disp['assessable'].mean():.1%})")

    ledger_cols = [
        "txn_id", "customer_id", "card_token", "card_product", "issuer", "ts", "day", "amount_inr",
        "TransactionAmt", "ProductCD", "merchandise_or_services", "product_description",
        "descriptor_text", "descriptor_is_clear", "channel", "status",
        "is_validation_charge", "disputed", "tc40_reported",
        "device_fingerprint", "device_id", "purchase_ip", "customer_email",
        "shipping_address", "customer_account_id", "isFraud",
        "dist1", "D1", "D2", "D3", "D4", "D10", "D15", "C1", "C2", "C5", "C13", "C14",
        "id_01", "id_02", "id_05", "id_06", "id_09", "id_11", "id_13", "id_17",
        "id_19", "id_20", "id_35", "id_36", "id_37", "id_38",
        "DeviceType", "DeviceInfo", "card4", "card6", "addr1",
        "V95", "V96", "V97", "V126", "V127", "V128", "V307", "V308", "V310",
    ]
    ledger = df[[c for c in ledger_cols if c in df.columns]].copy()
    ledger.to_parquet(OUT / "real_ledger.parquet", index=False)
    print(f"\nsaved -> {OUT / 'real_ledger.parquet'}  ({len(ledger):,} rows)")

    disp[["txn_id", "customer_id", "card_token", "ts", "day", "amount_inr",
          "assessable"]].to_parquet(OUT / "real_disputes.parquet", index=False)
    print(f"saved -> {OUT / 'real_disputes.parquet'}  ({len(disp):,} rows)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-age", type=int, default=120)
    ap.add_argument("--max-age", type=int, default=364)
    args = ap.parse_args()
    build(args.min_age, args.max_age)
