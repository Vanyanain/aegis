"""In-process data and model store for the AEGIS API.

Everything is loaded once at import and held in memory: the ledger is a few megabytes and
the models are hundreds of kilobytes, so there is no database to run and no cold-start
training. Cases are scored eagerly at startup, which keeps every endpoint a lookup rather
than an inference call and makes the console feel like a terminal instead of a web form.

The one thing NOT precomputed is CE 3.0 qualification detail. The gate returns a full
diagnosis -- matched elements, the specific blocking gap, the single-field counterfactual --
and that is computed per request against the rulebook, so a rulebook change takes effect
immediately without regenerating anything.
"""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from aegis.costlab import optimizer as CL
from aegis.fusion import intent as FI
from aegis.sidea import features as F
from rules import registry
from rules.vamp import v2026_04 as vamp_rules

ROOT = Path(__file__).resolve().parents[2]
DATA, MODELS = ROOT / "data", ROOT / "models"

# Columns the CE 3.0 gate reads from a transaction.
TXN_COLS = [
    "txn_id", "card_token", "ts", "status", "disputed", "tc40_reported",
    "is_validation_charge", "product_description", "merchandise_or_services",
    "purchase_ip", "device_fingerprint", "device_id", "customer_email",
    "customer_account_id", "shipping_address",
]


class Store:
    def __init__(self) -> None:
        self.ledger = pd.read_parquet(DATA / "ledger.parquet")
        self.cases = pd.read_parquet(DATA / "cases.parquet")
        self.evidence = pd.read_parquet(DATA / "evidence_features.parquet")

        self.sidea = joblib.load(MODELS / "sidea_winprob.joblib")
        self.sideb = joblib.load(MODELS / "sideb_forensics.joblib")
        self.fusion = joblib.load(MODELS / "fusion_intent.joblib")

        self._build_case_view()
        self._index()

    # --- setup ---------------------------------------------------------------------

    def _build_case_view(self) -> None:
        df = F.build(self.cases, self.ledger)

        m = self.sidea
        raw = m["model"].predict_proba(df[m["features"]])[:, 1]
        df["win_prob"] = m["calibrator"].predict(raw)

        # Side B's score becomes a Side A feature. At serving time this is an in-sample
        # score for items the forensic model trained on, which is unavoidable and correct --
        # production always uses the deployed model. The fusion model was TRAINED on
        # out-of-fold scores (see scripts/score_evidence_oof.py) so it never learned to rely
        # on a sharpness that will not exist for genuinely new evidence.
        df["evidence_tamper_score"] = self._tamper_scores(df["txn_id"])

        fm = self.fusion
        probs = fm["model"].predict_proba(df[fm["features"]])
        for i, name in enumerate(fm["intents"]):
            df[f"intent_{name}"] = probs[:, i]
        df["intent_top"] = [fm["intents"][i] for i in probs.argmax(axis=1)]

        # Attach evidence, where the customer submitted any.
        ev = self.evidence.set_index("txn_id")
        ev = ev[~ev.index.duplicated(keep="first")]
        df["evidence_item_id"] = df["txn_id"].map(ev["item_id"])
        df["has_evidence_file"] = df["evidence_item_id"].notna()

        self.view = df

    def _tamper_scores(self, txn_ids: pd.Series) -> pd.Series:
        ev = self.evidence.drop_duplicates("txn_id")
        X = ev[self.sideb["features"]].fillna(0.0)
        raw = self.sideb["model"].predict_proba(X)[:, 1]
        scores = pd.Series(self.sideb["calibrator"].predict(raw), index=ev["txn_id"].to_numpy())
        return txn_ids.map(scores).fillna(0.0).astype(float)

    def _index(self) -> None:
        self._by_dispute = self.view.set_index("dispute_id")
        cols = TXN_COLS + ["customer_id"]
        self._hist = {
            cid: g[TXN_COLS].to_dict("records")
            for cid, g in self.ledger[cols].groupby("customer_id")
        }
        self._txn = self.ledger.set_index("txn_id")
        self._ev_by_item = self.evidence.set_index("item_id")

    def has_dispute(self, dispute_id: str) -> bool:
        return dispute_id in self._by_dispute.index

    # --- CE 3.0 -------------------------------------------------------------------

    def qualify(self, dispute_id: str) -> dict[str, Any]:
        """Run the CE 3.0 rulebook live for one dispute, with full gap diagnosis."""
        if dispute_id not in self._by_dispute.index:
            raise KeyError(dispute_id)
        row = self._by_dispute.loc[dispute_id]
        txn = self._txn.loc[row["txn_id"]]
        disputed = {c: txn[c] for c in TXN_COLS if c != "txn_id"}
        disputed["txn_id"] = row["txn_id"]
        # The gate must see the transaction as it stood when the dispute was raised.
        disputed["disputed"] = False

        as_of = pd.Timestamp(row["dispute_date"]).date()
        ce3 = registry.ce3(as_of)
        history = [t for t in self._hist[row["customer_id"]] if t["txn_id"] != row["txn_id"]]
        q = ce3.qualify(disputed, history, reason_code=row["reason_code"])
        out = q.as_dict()

        # Attach the actual prior transactions so the console can render the element grid.
        if q.best_prior_pair:
            ids = list(q.best_prior_pair.prior_ids)
            out["prior_transactions"] = [
                self._txn_summary(i) for i in ids
            ]
        out["disputed_transaction"] = self._txn_summary(row["txn_id"])
        return out

    def _txn_summary(self, txn_id: str) -> dict[str, Any]:
        t = self._txn.loc[txn_id]
        return {
            "txn_id": txn_id,
            "ts": pd.Timestamp(t["ts"]).isoformat(),
            "amount_inr": float(t["amount_inr"]),
            "product_description": _s(t["product_description"]),
            "merchandise_or_services": _s(t["merchandise_or_services"]),
            "descriptor_text": _s(t["descriptor_text"]),
            "channel": _s(t["channel"]),
            "elements": {
                "purchase_ip": _s(t["purchase_ip"]),
                "device_fingerprint": _s(t["device_fingerprint"]),
                "device_id": _s(t["device_id"]),
                "customer_email": _s(t["customer_email"]),
                "customer_account_id": _s(t["customer_account_id"]),
                "shipping_address": _s(t["shipping_address"]),
            },
        }

    # --- evidence ------------------------------------------------------------------

    def evidence_verdict(self, item_id: str) -> dict[str, Any] | None:
        """Score one stored evidence item through the model and the rule layer."""
        if item_id not in self._ev_by_item.index:
            return None
        from aegis.sideb import rules as ER

        row = self._ev_by_item.loc[item_id]
        feats = {k: row[k] for k in self.sideb["features"] if k in row.index}
        X = pd.DataFrame([feats])[self.sideb["features"]].fillna(0.0)
        raw = self.sideb["model"].predict_proba(X)[:, 1][0]
        score = float(self.sideb["calibrator"].predict([raw])[0])

        rec = row.to_dict()
        flags = ER.evaluate(rec, rec.get("claimed_amount_inr"), rec.get("claimed_ts"),
                            bool(rec.get("descriptor_is_clear", True)))
        v = ER.verdict(score, flags)
        v["item_id"] = item_id
        v["path"] = str(row["path"])
        v["claimed_amount_inr"] = float(row["claimed_amount_inr"])
        # Ground-truth family is carried for the demo only, and never feeds the verdict.
        v["_ground_truth_family"] = str(row["family"])
        v["top_features"] = _top_forensic_features(feats)
        return v

    # --- portfolio -----------------------------------------------------------------

    def overview(self, econ: CL.Economics | None = None) -> dict[str, Any]:
        econ = econ or CL.Economics()
        v = self.view
        c104 = v[v["reason_code"] == "10.4"]
        # `qualified` is stored as int because the model consumes it numerically; masking
        # needs it back as a boolean.
        qual = c104["qualified"].astype(bool)
        naive = c104["naive_rule_qualified"].astype(bool)

        vamp = vamp_rules.VampState(
            monthly_transactions=econ.monthly_transactions,
            tc40_count=econ.tc40_count, tc15_count=econ.tc15_count, usd_inr=econ.usd_inr,
        )

        return {
            "portfolio": {
                "transactions": int(len(self.ledger)),
                "customers": int(self.ledger["customer_id"].nunique()),
                "disputes": int(len(v)),
                "disputes_104": int(len(c104)),
                "exposure_inr": float(v["amount_inr"].sum()),
                "exposure_104_inr": float(c104["amount_inr"].sum()),
            },
            "ce3": {
                "qualified_rate": float(qual.mean()) if len(c104) else 0.0,
                "qualified_count": int(qual.sum()),
                "defensible_inr": float(c104.loc[qual, "amount_inr"].sum()),
                "undefendable_inr": float(c104.loc[~qual, "amount_inr"].sum()),
                # The naive "any two of four" reading, shown for contrast. See RESEARCH.md s1.
                "naive_qualified_rate": float(naive.mean()) if len(c104) else 0.0,
                "naive_false_positives": int(((~qual) & naive).sum()),
                "naive_false_positive_inr": float(c104.loc[(~qual) & naive, "amount_inr"].sum()),
            },
            "capture_readiness": self.capture_readiness(),
            "vamp": vamp.as_dict(),
            "rules": registry.manifest(),
        }

    def capture_readiness(self) -> dict[str, Any]:
        """Coverage of each CE 3.0 element, and what fixing each one would unlock.

        This is the pre-dispute view: because the rulebook requires priors aged 120-364
        days, whatever the merchant starts capturing today governs which disputes become
        defensible next quarter. Coverage alone is not the answer, so this also counts the
        disputes each element would actually flip.
        """
        led = self.ledger
        recent = led[led["ts"] >= led["ts"].max() - pd.Timedelta(days=90)]
        v = self.view
        c104 = v[(v["reason_code"] == "10.4") & (~v["qualified"].astype(bool))]

        unlock_counts: dict[str, int] = {}
        unlock_value: dict[str, float] = {}
        for els, amt in zip(c104["unlock_elements"], c104["amount_inr"]):
            for e in [x for x in str(els).split(",") if x]:
                unlock_counts[e] = unlock_counts.get(e, 0) + 1
                unlock_value[e] = unlock_value.get(e, 0.0) + float(amt)

        elements = []
        for col, key in [("purchase_ip", "purchase_ip"),
                         ("device_fingerprint", "device_fp_or_id"),
                         ("device_id", "device_fp_or_id"),
                         ("customer_email", "customer_email"),
                         ("customer_account_id", "customer_account_id"),
                         ("shipping_address", "shipping_address")]:
            elements.append({
                "field": col,
                "element": key,
                "tier": "main" if key in ("purchase_ip", "device_fp_or_id") else "secondary",
                "coverage_all_time": float(led[col].notna().mean()),
                "coverage_last_90d": float(recent[col].notna().mean()),
                "would_unlock_cases": int(unlock_counts.get(key, 0)),
                "would_unlock_inr": float(unlock_value.get(key, 0.0)),
            })

        return {
            "elements": elements,
            "blocking_gaps": (
                v[v["reason_code"] == "10.4"]["primary_gap"].value_counts().to_dict()
            ),
            "lag_days": 120,
            "note": (
                "CE 3.0 requires prior transactions aged 120-364 days, so capture changes "
                "made today first affect qualification roughly 120 days from now. This is "
                "the pre-dispute lever: by the time a dispute arrives, the data either "
                "exists or it does not."
            ),
        }

    # --- queue ---------------------------------------------------------------------

    def disputes(self, limit: int = 100, only_104: bool = False,
                 econ: CL.Economics | None = None) -> list[dict[str, Any]]:
        econ = econ or CL.Economics()
        v = self.view
        if only_104:
            v = v[v["reason_code"] == "10.4"]
        break_even = CL.ev_threshold(v["amount_inr"].to_numpy(), econ)
        v = v.assign(break_even=break_even, worth_fighting=v["win_prob"].to_numpy() >= break_even)
        # Rank by rupees genuinely at stake, not by probability: a 90% chance on Rs 300 is
        # worth less attention than a 40% chance on Rs 40,000.
        v = v.assign(expected_recovery=v["win_prob"] * v["amount_inr"])
        v = v.sort_values("expected_recovery", ascending=False).head(limit)
        return [self._case_row(r) for _, r in v.iterrows()]

    def _case_row(self, r: pd.Series) -> dict[str, Any]:
        return {
            "dispute_id": r["dispute_id"],
            "txn_id": r["txn_id"],
            "customer_id": r["customer_id"],
            "reason_code": r["reason_code"],
            "dispute_date": pd.Timestamp(r["dispute_date"]).date().isoformat(),
            "amount_inr": float(r["amount_inr"]),
            "qualified": bool(r["qualified"]),
            "n_matched": int(r["n_matched"]),
            "matched_elements": [e for e in str(r["matched_elements"]).split(",") if e],
            "primary_gap": _s(r["primary_gap"]),
            "win_prob": float(r["win_prob"]),
            "break_even": float(r.get("break_even", 0.0)),
            "worth_fighting": bool(r.get("worth_fighting", False)),
            "expected_recovery_inr": float(r.get("expected_recovery", 0.0)),
            "intent_top": r["intent_top"],
            "intent": {k: float(r[f"intent_{k}"]) for k in FI.INTENTS},
            "has_evidence": bool(r["has_evidence_file"]),
            "evidence_item_id": _s(r["evidence_item_id"]),
            "tc40_reported": bool(r["tc40_reported"]),
            "category": _s(r["category"]),
            "channel": _s(r["channel"]),
            "descriptor_is_clear": bool(r["descriptor_is_clear"]),
        }

    def case(self, dispute_id: str, econ: CL.Economics | None = None) -> dict[str, Any]:
        econ = econ or CL.Economics()
        if dispute_id not in self._by_dispute.index:
            raise KeyError(dispute_id)
        # dispute_id is this frame's INDEX, so it has to be put back on the row before
        # _case_row (which is also fed rows from self.view, where it is a column) can read it.
        r = self._by_dispute.loc[dispute_id].copy()
        r["dispute_id"] = dispute_id
        base = self._case_row(r)
        amt = float(r["amount_inr"])
        break_even = float(CL.ev_threshold(np.array([amt]), econ)[0])
        base["break_even"] = break_even
        base["worth_fighting"] = base["win_prob"] >= break_even
        base["expected_recovery_inr"] = base["win_prob"] * amt

        base["qualification"] = self.qualify(dispute_id)

        ev = None
        if base["evidence_item_id"]:
            ev = self.evidence_verdict(base["evidence_item_id"])
        base["evidence"] = ev

        rec = FI.recommend(
            base["intent"], base["qualified"], base["win_prob"],
            ev["label"] if ev else None, amt, break_even,
        )
        base["recommendation"] = {
            "action": rec.action, "tone": rec.tone,
            "rationale": rec.rationale, "confidence": rec.confidence,
        }
        base["explanation"] = self.explain(dispute_id)
        return base

    def explain(self, dispute_id: str) -> list[dict[str, Any]]:
        """SHAP contributions for the win-probability model on one case."""
        r = self._by_dispute.loc[[dispute_id]]
        m = self.sidea
        X = r[m["features"]]
        contrib = m["model"].predict_proba(X, pred_contrib=True)[0]
        names = list(m["features"]) + ["_base"]
        pairs = sorted(zip(names, contrib), key=lambda kv: -abs(kv[1]))
        return [
            {"feature": k, "contribution": float(v),
             "value": _jsonable(r.iloc[0][k]) if k in r.columns else None}
            for k, v in pairs[:10] if k != "_base"
        ]


def _top_forensic_features(feats: dict[str, float], n: int = 6) -> list[dict[str, Any]]:
    """The forensic readings most worth showing a human, with plain-language labels."""
    labels = {
        "arith_components_vs_total_rel": "Subtotal + tax vs printed total",
        "arith_items_vs_subtotal_rel": "Line items vs printed subtotal",
        "arith_cgst_sgst_mismatch": "CGST / SGST split",
        "arith_implied_gst": "Implied GST rate (%)",
        "xc_amount_rel_diff": "Receipt total vs settled amount",
        "xc_date_days": "Receipt date vs transaction date (days)",
        "ela_block_dispersion": "Error-level analysis, block dispersion",
        "ela_max_block": "Error-level analysis, hottest block",
        "typo_baseline_drift": "Baseline drift (thermal print jitter)",
        "typo_gap_cv": "Character spacing regularity",
        "typo_conf_mean": "OCR confidence (mean)",
        "noise_uniformity": "Sensor-noise uniformity",
        "exif_has_camera": "Camera make/model in EXIF",
        "jpeg_q_luma_mean": "JPEG quantisation (luma mean)",
    }
    out = []
    for k, label in labels.items():
        if k in feats:
            out.append({"feature": k, "label": label, "value": float(feats[k])})
    return out[:n] if n else out


def _s(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and v != v) or pd.isna(v):
        return None
    return str(v)


def _jsonable(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    return _s(v)


@lru_cache(maxsize=1)
def get_store() -> Store:
    return Store()
