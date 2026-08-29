"""Abuse-ring linkage: find coordinated fraud through shared device identity.

THE ONLY LINK THAT SURVIVES SCRUTINY.

The obvious plan is to link disputes on shared email, shipping address, device and card.
Measured against real IEEE-CIS data, three of those four are useless:

    device_fingerprint    9,743 distinct,   14.4 transactions each   <- usable
    card_product         14,893 distinct,   39.7 transactions each   <- issuer/BIN level
    shipping_address        437 distinct,  1,201 transactions each   <- addr1 is a REGION code
    customer_email           59 distinct,  8,408 transactions each   <- a DOMAIN, not an address

Linking strangers because they both live in a region or both use gmail.com would generate
enormous, meaningless "rings". Only the device fingerprint identifies a shared physical
machine, which is what a ring actually is: many stolen credentials pushed through one
device. `card_product` is retained as a weak corroborating edge only, never on its own.

So the graph is: nodes are resolved customer entities, and an edge exists when two entities
transacted from the same device fingerprint. A connected component containing more than one
entity is a candidate ring -- one machine, several "customers".

This is defence-only. It surfaces coordination already present in the merchant's own
transaction record; it does nothing to enable it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

# A device shared by more entities than this is an artefact, not a ring: shared kiosks,
# corporate NAT, emulator farms used by the dataset's own instrumentation, or simply a
# fingerprint too coarse to identify a machine. Including them would swamp the result.
# A device legitimately shared by a handful of accounts is a family or an office machine.
# Beyond that it stops identifying anyone.
MAX_ENTITIES_PER_DEVICE = 6

# Below this a "ring" is just a household or one person with two cards.
MIN_RING_ENTITIES = 3

# Connected components on a shared-identifier graph produce a giant component: A shares a
# device with B, B with C, and transitively half the portfolio becomes one "ring". At a
# 40-entity device cap the largest component held 16,415 entities and 73,242 transactions --
# not a ring, just the transitive closure of shared-device noise, and useless to an analyst.
# Components above this size are excluded from the ring list and reported separately as a
# hairball rather than dressed up as a detection. The lift measurement below is computed on
# the actionable rings only, so it cannot be inflated by the hairball.
MAX_RING_ENTITIES = 60


@dataclass
class Ring:
    ring_id: int
    entities: list[str]
    devices: list[str]
    n_transactions: int
    n_chargebacks: int
    value_inr: float
    chargeback_value_inr: float
    chargeback_rate: float
    shared_card_products: int = 0
    members: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ring_id": self.ring_id,
            "n_entities": len(self.entities),
            "n_devices": len(self.devices),
            "n_transactions": self.n_transactions,
            "n_chargebacks": self.n_chargebacks,
            "value_inr": self.value_inr,
            "chargeback_value_inr": self.chargeback_value_inr,
            "chargeback_rate": self.chargeback_rate,
            "shared_card_products": self.shared_card_products,
            "entities": self.entities[:25],
            "devices": self.devices[:10],
        }


def build_rings(ledger: pd.DataFrame) -> tuple[list[Ring], pd.DataFrame]:
    """Cluster customer entities that share a device fingerprint.

    Returns the rings plus a per-entity frame carrying its ring id, so ring membership can
    be joined onto any case view.
    """
    df = ledger[ledger["device_fingerprint"].notna()][
        ["customer_id", "device_fingerprint", "card_product", "amount_inr", "disputed"]
    ].copy()
    if df.empty:
        return [], pd.DataFrame(columns=["customer_id", "ring_id"])

    # Drop devices seen by implausibly many entities before building any edges.
    per_dev = df.groupby("device_fingerprint")["customer_id"].nunique()
    keep = per_dev[(per_dev > 1) & (per_dev <= MAX_ENTITIES_PER_DEVICE)].index
    df = df[df["device_fingerprint"].isin(keep)]
    if df.empty:
        return [], pd.DataFrame(columns=["customer_id", "ring_id"])

    ents = pd.Index(sorted(df["customer_id"].unique()))
    devs = pd.Index(sorted(df["device_fingerprint"].unique()))
    ei = df["customer_id"].map({e: i for i, e in enumerate(ents)}).to_numpy()
    di = df["device_fingerprint"].map({d: i for i, d in enumerate(devs)}).to_numpy()

    # Bipartite entity-device incidence; connected components over it give the rings
    # without materialising an entity-by-entity edge list.
    n_e, n_d = len(ents), len(devs)
    inc = coo_matrix(
        (np.ones(len(df)), (ei, n_e + di)), shape=(n_e + n_d, n_e + n_d)
    )
    n_comp, labels = connected_components(inc + inc.T, directed=False)

    ent_label = labels[:n_e]
    ent_ring = pd.DataFrame({"customer_id": ents, "component": ent_label})

    # Full per-entity stats come from the WHOLE ledger, not just device-bearing rows, so a
    # ring's value reflects everything its members did.
    stats = (
        ledger.groupby("customer_id")
        .agg(n_txn=("txn_id", "size"), value=("amount_inr", "sum"),
             n_cb=("disputed", "sum"),
             cb_value=("amount_inr", lambda s: 0.0))
        .reset_index()
    )
    cb_val = (
        ledger[ledger["disputed"]].groupby("customer_id")["amount_inr"].sum()
        .rename("cb_value_real").reset_index()
    )
    stats = stats.merge(cb_val, on="customer_id", how="left")
    stats["cb_value_real"] = stats["cb_value_real"].fillna(0.0)

    merged = ent_ring.merge(stats, on="customer_id", how="left").fillna(
        {"n_txn": 0, "value": 0.0, "n_cb": 0, "cb_value_real": 0.0}
    )
    dev_by_comp = (
        df.assign(component=df["customer_id"].map(dict(zip(ents, ent_label))))
        .groupby("component")["device_fingerprint"].unique()
    )
    cp_by_comp = (
        df.assign(component=df["customer_id"].map(dict(zip(ents, ent_label))))
        .groupby("component")["card_product"].nunique()
    )

    rings: list[Ring] = []
    rid = 0
    hairballs = []
    for comp, grp in merged.groupby("component"):
        if len(grp) < MIN_RING_ENTITIES:
            continue
        if len(grp) > MAX_RING_ENTITIES:
            hairballs.append({"n_entities": int(len(grp)),
                              "n_transactions": int(grp["n_txn"].sum()),
                              "n_chargebacks": int(grp["n_cb"].sum())})
            continue
        n_cb = int(grp["n_cb"].sum())
        n_tx = int(grp["n_txn"].sum())
        rings.append(Ring(
            ring_id=rid,
            entities=list(grp["customer_id"]),
            devices=list(dev_by_comp.get(comp, [])),
            n_transactions=n_tx,
            n_chargebacks=n_cb,
            value_inr=float(grp["value"].sum()),
            chargeback_value_inr=float(grp["cb_value_real"].sum()),
            chargeback_rate=float(n_cb / n_tx) if n_tx else 0.0,
            shared_card_products=int(cp_by_comp.get(comp, 0)),
        ))
        rid += 1

    rings.sort(key=lambda r: -r.chargeback_value_inr)
    for i, r in enumerate(rings):
        r.ring_id = i

    lookup = pd.DataFrame(
        [(e, r.ring_id) for r in rings for e in r.entities],
        columns=["customer_id", "ring_id"],
    )
    build_rings.last_hairballs = hairballs  # type: ignore[attr-defined]
    return rings, lookup


def summarise(rings: list[Ring], ledger: pd.DataFrame, lookup: pd.DataFrame) -> dict:
    """Does ring membership actually mean anything?

    The test that matters: compare the chargeback rate inside rings against the portfolio
    baseline. If they are the same, the clustering has found nothing and should be said so.
    """
    in_ring = set(lookup["customer_id"])
    led = ledger.assign(in_ring=ledger["customer_id"].isin(in_ring))
    base = float(led.loc[~led["in_ring"], "disputed"].mean())
    ring = float(led.loc[led["in_ring"], "disputed"].mean()) if led["in_ring"].any() else 0.0
    return {
        "n_rings": len(rings),
        "n_entities_in_rings": int(len(in_ring)),
        "n_transactions_in_rings": int(led["in_ring"].sum()),
        "chargeback_rate_in_rings": ring,
        "chargeback_rate_outside": base,
        "lift": float(ring / base) if base > 0 else 0.0,
        "chargeback_value_in_rings_inr": float(
            led.loc[led["in_ring"] & led["disputed"], "amount_inr"].sum()
        ),
        "largest_rings": [r.as_dict() for r in rings[:10]],
        "excluded_hairballs": getattr(build_rings, "last_hairballs", []),
        "method": (
            "Entities linked by shared device fingerprint; devices touching more than "
            f"{MAX_ENTITIES_PER_DEVICE} entities discarded as non-identifying; components "
            f"of at least {MIN_RING_ENTITIES} entities reported."
        ),
    }
