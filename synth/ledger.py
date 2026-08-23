"""Synthetic CE 3.0-shaped transaction ledger.

WHY THIS EXISTS. Every public fraud dataset is a flat table of anonymised transactions.
None of them carry device fingerprints, purchase IPs, account IDs and shipping addresses
linked across a customer's multi-order history -- which is precisely and only what CE 3.0
qualification is computed over. So the rulebook in rules/ce3 cannot be exercised, let alone
evaluated, on any open data that exists. We synthesise, and we disclose it loudly:
docs/MODEL_CARD.md states the limits and synth/cards/ledger_card.md documents the process.

THE DESIGN POINT. The interesting variable is not customer behaviour, it is DATA CAPTURE.
Merchants fail CE 3.0 because their pipeline never stored a device fingerprint, or forwarded
the gateway's IP instead of the client's, or treated shipping address as a fulfilment detail
rather than evidence. So capture coverage here is deliberately partial, channel-dependent
and TIME-VARYING: the merchant "switches on" device fingerprinting partway through the
window, which is exactly the event whose 120-day-lagged payoff the product is built to show.

HONESTY GUARD. Outcome labels are drawn from a documented structural model, so a model
trained to recover them is partly circular. Three mitigations, all restated in the model
card: (1) a per-issuer random effect that is never exposed as a feature, plus substantial
label noise; (2) grouped splitting by customer_id everywhere downstream; (3) the explicit
framing that these metrics measure recovery of a known structure under noise and are not
external-validity claims.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

SEED = 20260822

# --- Window ------------------------------------------------------------------------------

WINDOW_START = date(2024, 6, 1)
WINDOW_END = date(2026, 8, 1)

# Disputes are only raised in the final year, so that the 120-364 day prior window has
# something to look at. Raising disputes uniformly across the whole window would make most
# cases fail on "no priors old enough", which is realistic for a new merchant but useless
# for evaluating the rest of the rulebook.
DISPUTE_WINDOW_START = date(2025, 8, 1)

# The merchant enables device fingerprinting on this date. Everything before it is blind.
# This single line is the product thesis in executable form.
DEVICE_FP_ENABLED_FROM = date(2025, 3, 1)
MOBILE_SDK_DEVICE_ID_FROM = date(2024, 11, 1)

# --- Scale -------------------------------------------------------------------------------

N_CUSTOMERS = 7000
TARGET_TRANSACTIONS = 120000
DISPUTE_RATE = 0.040

# --- Channels ----------------------------------------------------------------------------

CHANNELS = ("web", "app", "subscription_rebill")
CHANNEL_P = (0.45, 0.40, 0.15)

# Per-channel probability that each CE 3.0 element is captured at all.
# The subscription_rebill column is the realistic horror story: card-on-file rebills are
# server-initiated, so there is no browser, no device, and often no client IP -- and
# unrecognised recurring charges are a top source of first-party misuse. The transactions
# most likely to be disputed are the ones least likely to qualify.
CAPTURE_P = {
    "web": {"purchase_ip": 0.97, "device_fingerprint": 0.92, "device_id": 0.00},
    "app": {"purchase_ip": 0.70, "device_fingerprint": 0.00, "device_id": 0.88},
    "subscription_rebill": {"purchase_ip": 0.15, "device_fingerprint": 0.00, "device_id": 0.10},
}

# Probability the value STAYS the same as the customer's anchor value on a given order.
# Mobile IPs churn hard; device identifiers are sticky until the customer changes handset.
STABILITY = {
    "web": {"purchase_ip": 0.80, "device": 0.94},
    "app": {"purchase_ip": 0.35, "device": 0.90},
    "subscription_rebill": {"purchase_ip": 0.55, "device": 0.90},
}

CATEGORIES = {
    "electronics": ("merchandise", 4500, 45000),
    "apparel": ("merchandise", 800, 6000),
    "home": ("merchandise", 1200, 18000),
    "beauty": ("merchandise", 400, 3500),
    "streaming": ("services", 149, 899),
    "saas": ("services", 499, 9999),
    "gaming_credits": ("services", 99, 4999),
    "travel": ("services", 2500, 60000),
}
CATEGORY_P = (0.14, 0.20, 0.11, 0.13, 0.16, 0.10, 0.10, 0.06)

PRODUCTS = {
    "electronics": ["Wireless earbuds", "USB-C hub", "Mechanical keyboard", "Power bank 20000mAh", "Action camera"],
    "apparel": ["Cotton kurta", "Running shoes", "Denim jacket", "Linen shirt", "Wool scarf"],
    "home": ["Air purifier filter", "Ceramic dinner set", "Memory foam pillow", "Table lamp", "Cookware set"],
    "beauty": ["Vitamin C serum", "Sunscreen SPF50", "Hair oil 200ml", "Matte lipstick", "Face cleanser"],
    "streaming": ["Monthly streaming plan", "Annual streaming plan", "Premium tier upgrade"],
    "saas": ["Team plan seat", "Pro plan monthly", "Storage add-on 100GB", "API usage tier 2"],
    "gaming_credits": ["1200 game credits", "Season pass", "Cosmetic bundle", "Starter pack"],
    "travel": ["Domestic flight booking", "Hotel night Goa", "Cab airport transfer", "Travel insurance"],
}

# Descriptor clarity drives "I don't recognise this charge" disputes. A descriptor like
# "SVCS*BLR 4471" is a friendly-fraud generator all by itself.
CLEAR_DESCRIPTORS = {
    "electronics": "AEGISMART ELECTRONICS",
    "apparel": "AEGISMART APPAREL",
    "home": "AEGISMART HOME",
    "beauty": "AEGISMART BEAUTY",
    "streaming": "AEGISMART STREAM",
    "saas": "AEGISMART SAAS",
    "gaming_credits": "AEGISMART GAMES",
    "travel": "AEGISMART TRAVEL",
}
OPAQUE_DESCRIPTORS = ["SVCS*BLR 4471", "DIGITAL PURCHASE", "IN*MERCHSVC", "PMT*ONLINE 8821"]

ISSUERS = ["HDFC", "ICICI", "SBI", "AXIS", "KOTAK", "IDFC", "YES", "PNB"]

INTENTS = ("criminal_fraud", "first_party_misuse", "genuine_service_failure")
INTENT_P = (0.15, 0.60, 0.25)

# Reason code given latent intent. Criminal fraud almost always surfaces as 10.4; genuine
# service failures mostly surface as non-fraud codes.
RC_BY_INTENT = {
    "criminal_fraud": (("10.4", "13.1"), (0.95, 0.05)),
    "first_party_misuse": (("10.4", "13.1", "13.3"), (0.70, 0.20, 0.10)),
    "genuine_service_failure": (("10.4", "13.1", "13.3"), (0.10, 0.45, 0.45)),
}


def _h(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


@dataclass
class Customer:
    customer_id: str
    card_token: str
    issuer: str
    email: str
    account_id: str | None
    shipping_address: str | None
    anchor_ip: str
    anchor_device_fp: str
    anchor_device_id: str
    home_channel: str
    tenure_start: date


def _make_customers(rng: np.random.Generator) -> list[Customer]:
    customers = []
    for i in range(N_CUSTOMERS):
        cid = f"CUST{i:06d}"
        home_channel = str(rng.choice(CHANNELS, p=CHANNEL_P))
        # 30% of customers shop as guests and never get an account ID -- a silent CE 3.0 killer.
        has_account = rng.random() < 0.70
        # Services-only customers have no shipping address, ever.
        has_shipping = rng.random() < 0.72
        span = (WINDOW_END - WINDOW_START).days
        # Tenure is front-loaded: most customers were acquired early, some arrive late and
        # will structurally fail the 120-day prior test no matter what is captured.
        start_offset = int(rng.beta(1.4, 3.0) * span)
        customers.append(
            Customer(
                customer_id=cid,
                card_token=f"tok_{_h(cid, 'card')}",
                issuer=str(rng.choice(ISSUERS)),
                email=f"user{i}@{rng.choice(['gmail.com', 'outlook.com', 'yahoo.in', 'proton.me'])}",
                account_id=f"acct_{_h(cid, 'acct')}" if has_account else None,
                shipping_address=_h(cid, "ship") if has_shipping else None,
                anchor_ip=f"{rng.integers(1, 224)}.{rng.integers(0, 256)}.{rng.integers(0, 256)}.{rng.integers(1, 255)}",
                anchor_device_fp=f"fp_{_h(cid, 'fp')}",
                anchor_device_id=f"did_{_h(cid, 'did')}",
                home_channel=home_channel,
                tenure_start=WINDOW_START + timedelta(days=start_offset),
            )
        )
    return customers


def _capture(
    rng: np.random.Generator,
    cust: Customer,
    channel: str,
    ts: date,
) -> dict[str, str | None]:
    """Decide which CE 3.0 elements this transaction actually carries, and their values.

    Two independent gates, which is what makes the data realistic: the field must be
    CAPTURED at all (channel + era), and then it must be STABLE (same value as the
    customer's anchor). A captured-but-churned IP is present in the ledger and still fails
    to match -- the failure mode a coverage-only view of the problem would miss entirely.
    """
    out: dict[str, str | None] = {}
    cp = CAPTURE_P[channel]
    st = STABILITY[channel]

    # Purchase IP.
    if rng.random() < cp["purchase_ip"]:
        if rng.random() < st["purchase_ip"]:
            out["purchase_ip"] = cust.anchor_ip
        else:
            out["purchase_ip"] = f"{rng.integers(1, 224)}.{rng.integers(0, 256)}.{rng.integers(0, 256)}.{rng.integers(1, 255)}"
    else:
        out["purchase_ip"] = None

    # Device fingerprint: web only, and only after the merchant switched it on.
    if ts >= DEVICE_FP_ENABLED_FROM and rng.random() < cp["device_fingerprint"]:
        out["device_fingerprint"] = (
            cust.anchor_device_fp if rng.random() < st["device"] else f"fp_{_h(cust.customer_id, ts, 'alt')}"
        )
    else:
        out["device_fingerprint"] = None

    # Device ID: mobile SDK only, after SDK rollout.
    if ts >= MOBILE_SDK_DEVICE_ID_FROM and rng.random() < cp["device_id"]:
        out["device_id"] = (
            cust.anchor_device_id if rng.random() < st["device"] else f"did_{_h(cust.customer_id, ts, 'alt')}"
        )
    else:
        out["device_id"] = None

    # Email is nearly always captured, and nearly always stable.
    out["customer_email"] = cust.email if rng.random() < 0.985 else None

    # Account ID exists only for logged-in customers, and rebills often lose the linkage.
    if cust.account_id and rng.random() < (0.55 if channel == "subscription_rebill" else 0.94):
        out["customer_account_id"] = cust.account_id
    else:
        out["customer_account_id"] = None

    return out


def generate(seed: int = SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate the ledger and the dispute table. Deterministic given `seed`."""
    rng = np.random.default_rng(seed)
    customers = _make_customers(rng)

    # Per-issuer random effect. This is a genuine confounder on the win outcome that is
    # NEVER written to the feature set -- it is the irreducible noise floor that stops the
    # downstream model from trivially inverting the generative process.
    issuer_effect = {iss: float(rng.normal(0, 0.60)) for iss in ISSUERS}

    rows = []
    per_customer_target = TARGET_TRANSACTIONS / N_CUSTOMERS
    for cust in customers:
        # Order count is over-dispersed: a few customers drive most volume, as in reality.
        n_orders = 1 + int(rng.gamma(shape=1.6, scale=per_customer_target / 1.6))
        n_orders = min(n_orders, 60)
        days_available = max(1, (WINDOW_END - cust.tenure_start).days)

        offsets = np.sort(rng.uniform(0, days_available, size=n_orders))
        is_subscriber = cust.home_channel == "subscription_rebill"
        if is_subscriber and n_orders > 3:
            # Subscribers rebill on a near-monthly cadence rather than at random.
            offsets = np.sort(
                np.clip(
                    np.arange(n_orders) * 30.4 + rng.normal(0, 1.5, size=n_orders),
                    0,
                    days_available,
                )
            )

        cat = str(rng.choice(list(CATEGORIES.keys()), p=CATEGORY_P))
        if is_subscriber:
            cat = str(rng.choice(["streaming", "saas", "gaming_credits"], p=(0.5, 0.3, 0.2)))
        mos, lo, hi = CATEGORIES[cat]
        descriptor_is_clear = rng.random() < 0.72

        for k, off in enumerate(offsets):
            ts = cust.tenure_start + timedelta(days=float(off))
            if ts > WINDOW_END:
                continue
            channel = (
                cust.home_channel
                if (is_subscriber or rng.random() < 0.82)
                else str(rng.choice(CHANNELS, p=CHANNEL_P))
            )
            amount = float(np.round(rng.uniform(lo, hi), 2))
            if is_subscriber:
                amount = float(np.round(rng.uniform(lo, min(hi, lo * 3)), 2))

            elems = _capture(rng, cust, channel, ts)
            ship = (
                cust.shipping_address
                if (mos == "merchandise" and cust.shipping_address and rng.random() < 0.96)
                else None
            )

            # Product description is missing on a slice of orders -- a hard CE 3.0 blocker
            # that costs nothing to fix and that merchants routinely overlook.
            has_desc = rng.random() < 0.93
            product = str(rng.choice(PRODUCTS[cat]))

            rows.append(
                {
                    "txn_id": f"TXN{len(rows):07d}",
                    "customer_id": cust.customer_id,
                    "card_token": cust.card_token,
                    "issuer": cust.issuer,
                    "ts": datetime.combine(ts, datetime.min.time()) + timedelta(
                        hours=int(rng.integers(0, 24)), minutes=int(rng.integers(0, 60))
                    ),
                    "amount_inr": amount,
                    "category": cat,
                    "merchandise_or_services": mos,
                    "product_description": product if has_desc else None,
                    "descriptor_text": (
                        CLEAR_DESCRIPTORS[cat] if descriptor_is_clear else str(rng.choice(OPAQUE_DESCRIPTORS))
                    ),
                    "descriptor_is_clear": descriptor_is_clear,
                    "channel": channel,
                    "status": "paid" if rng.random() < 0.985 else "refunded",
                    "is_validation_charge": bool(rng.random() < 0.012),
                    "order_index": k,
                    "purchase_ip": elems["purchase_ip"],
                    "device_fingerprint": elems["device_fingerprint"],
                    "device_id": elems["device_id"],
                    "customer_email": elems["customer_email"],
                    "customer_account_id": elems["customer_account_id"],
                    "shipping_address": ship,
                    "avs_match": bool(rng.random() < (0.88 if ship else 0.0)),
                    "cvv_match": bool(rng.random() < 0.94),
                    "threeds_status": str(
                        rng.choice(
                            ["authenticated", "attempted", "not_authenticated"], p=(0.42, 0.23, 0.35)
                        )
                    ),
                    "delivery_confirmed": bool(
                        rng.random() < (0.86 if mos == "merchandise" else 0.0)
                    ),
                    # Post-purchase consumption: strong evidence against "never received it".
                    "post_purchase_usage_days": (
                        int(rng.integers(0, 45)) if mos == "services" else 0
                    ),
                    "disputed": False,
                    "tc40_reported": False,
                    "dispute_reason_code": None,
                    "dispute_date": None,
                    "is_validation": False,
                }
            )

    ledger = pd.DataFrame(rows)
    ledger["ts"] = pd.to_datetime(ledger["ts"])

    disputes = _generate_disputes(ledger, rng, issuer_effect)

    # Reflect dispute status back onto the ledger so the CE 3.0 gate can exclude previously
    # disputed and TC40-reported priors, exactly as the real rule requires.
    d_idx = disputes.set_index("txn_id")
    ledger = ledger.set_index("txn_id")
    ledger.loc[d_idx.index, "disputed"] = True
    ledger.loc[d_idx.index, "dispute_reason_code"] = d_idx["reason_code"]
    ledger.loc[d_idx.index, "dispute_date"] = d_idx["dispute_date"]
    ledger.loc[d_idx.index, "tc40_reported"] = d_idx["tc40_reported"]
    ledger = ledger.reset_index()

    # Standalone TC40s: issuer files a fraud report that never becomes a chargeback. These
    # count against the VAMP ratio and, since 18 April 2026, are CE 3.0-challengeable.
    undisputed = ledger.index[~ledger["disputed"]].to_numpy()
    n_standalone = int(len(undisputed) * 0.006)
    picks = rng.choice(undisputed, size=n_standalone, replace=False)
    ledger.loc[picks, "tc40_reported"] = True

    return ledger, disputes


def _generate_disputes(
    ledger: pd.DataFrame,
    rng: np.random.Generator,
    issuer_effect: dict[str, float],
) -> pd.DataFrame:
    """Pick disputed transactions and draw their latent intent and outcome."""
    eligible = ledger[
        (ledger["ts"].dt.date >= DISPUTE_WINDOW_START) & (ledger["status"] == "paid")
    ].copy()

    # Dispute propensity is not uniform. Opaque descriptors, subscription rebills and
    # digital goods attract first-party misuse; that correlation is the whole reason a
    # behavioural intent model has anything to learn.
    w = np.ones(len(eligible))
    w += (~eligible["descriptor_is_clear"]).to_numpy() * 1.5
    w += (eligible["channel"] == "subscription_rebill").to_numpy() * 1.4
    w += (eligible["merchandise_or_services"] == "services").to_numpy() * 0.5
    w += (eligible["amount_inr"] > 15000).to_numpy() * 0.4
    w = w / w.sum()

    n_disputes = int(len(eligible) * DISPUTE_RATE)
    picks = rng.choice(len(eligible), size=n_disputes, replace=False, p=w)
    sel = eligible.iloc[picks].copy()

    recs = []
    for _, t in sel.iterrows():
        intent = str(rng.choice(INTENTS, p=INTENT_P))
        # Opaque descriptors push cases toward genuine confusion rather than deliberate abuse.
        if not t["descriptor_is_clear"] and intent == "criminal_fraud" and rng.random() < 0.35:
            intent = "first_party_misuse"

        codes, probs = RC_BY_INTENT[intent]
        rc = str(rng.choice(codes, p=probs))
        dispute_date = t["ts"].date() + timedelta(days=int(rng.integers(8, 75)))
        if dispute_date > WINDOW_END:
            dispute_date = WINDOW_END

        # A TC40 accompanies most fraud-coded disputes. This is the double-count that makes
        # the VAMP ratio roughly twice what a naive chargebacks/transactions view suggests.
        tc40 = rc == "10.4" and rng.random() < 0.86

        # Customer-submitted supporting evidence -- the Side B trigger. Non-fraud codes
        # attract far more submitted "proof" than fraud codes do.
        p_evidence = {"10.4": 0.18, "13.1": 0.55, "13.3": 0.74}[rc]
        has_evidence = rng.random() < p_evidence

        recs.append(
            {
                "dispute_id": f"DSP{len(recs):06d}",
                "txn_id": t["txn_id"],
                "customer_id": t["customer_id"],
                "card_token": t["card_token"],
                "issuer": t["issuer"],
                "reason_code": rc,
                "dispute_date": dispute_date,
                "amount_inr": float(t["amount_inr"]),
                "latent_intent": intent,
                "tc40_reported": tc40,
                "has_customer_evidence": has_evidence,
                "issuer_effect": issuer_effect[t["issuer"]],
            }
        )

    disputes = pd.DataFrame(recs)
    disputes["dispute_date"] = pd.to_datetime(disputes["dispute_date"])
    return disputes


def draw_win_outcomes(
    disputes: pd.DataFrame,
    qualified: np.ndarray,
    n_matched: np.ndarray,
    delivery_confirmed: np.ndarray,
    descriptor_clear: np.ndarray,
    threeds_auth: np.ndarray,
    seed: int = SEED + 1,
) -> np.ndarray:
    """Draw `won_if_represented` from the documented structural model.

    Coefficients are chosen so the marginal win rates land on published priors: roughly
    15-20% for standard evidence on 10.4 friendly fraud, and 40-60% once CE 3.0 qualifies.
    The per-issuer effect enters here but is never exposed as a feature, and the residual
    noise term is deliberately large. Both exist so that downstream metrics reflect a hard
    prediction problem rather than an invertible formula.

    This function is the single place where outcome labels are created. Nothing downstream
    may reference `latent_intent` or `issuer_effect` as a model input.
    """
    rng = np.random.default_rng(seed)
    n = len(disputes)

    # Base is set so that a TYPICAL unqualified case -- which already carries the
    # first-party-misuse, delivery and descriptor lifts below -- lands at the published
    # 15-20% standard-evidence win rate, not so that the intercept alone does.
    logit = np.full(n, -2.80)
    logit += 1.40 * qualified.astype(float)
    logit += 0.42 * np.clip(n_matched - 2, 0, 3)  # extra matched elements help, with diminishing effect

    intent = disputes["latent_intent"].to_numpy()
    logit += np.where(intent == "first_party_misuse", 0.72, 0.0)
    logit += np.where(intent == "criminal_fraud", -0.92, 0.0)
    logit += np.where(intent == "genuine_service_failure", -0.50, 0.0)

    logit += 0.48 * delivery_confirmed.astype(float)
    logit += 0.38 * descriptor_clear.astype(float)
    logit += 0.30 * threeds_auth.astype(float)
    logit += disputes["issuer_effect"].to_numpy()          # hidden confounder
    logit += rng.normal(0, 0.55, size=n)                    # irreducible noise

    p = 1.0 / (1.0 + np.exp(-logit))
    return (rng.random(n) < p).astype(int)
