"""AEGIS API.

Serves the console and the JSON behind it. Every endpoint reads from an in-memory store
built at startup (see store.py), so the service has no database dependency and starts cold
in a couple of seconds.

Two things this API deliberately does NOT do:

* It never auto-submits a representment. The packet endpoint assembles evidence for a human
  to review and export. Submitting to a network on a merchant's behalf, on a model's say-so,
  is not a decision software should take.
* It never generates fake evidence. The forensic model detects tampering; the fake families
  used to train it are built offline by synth/ and are not reachable from any route here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# NOTE: do not import torch here. This environment's miniforge builds of torch and
# LightGBM link different OpenMP runtimes, and loading a LightGBM booster after torch has
# initialised OpenMP segfaults the interpreter (SIGSEGV, no traceback). Side B runs entirely
# on classical forensic features and gradient boosting, so torch is not needed at serving
# time at all -- the safest fix is simply not to pull it in.

from aegis.api.store import ROOT, get_store  # noqa: E402
from aegis.costlab import optimizer as CL  # noqa: E402
from aegis.evidence_log.chain import EvidenceLog  # noqa: E402
from aegis.packet import builder as PB  # noqa: E402
from rules import registry  # noqa: E402

app = FastAPI(
    title="AEGIS",
    description="Two-sided evidence-integrity engine for friendly-fraud disputes.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)

LOG = EvidenceLog(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
DOCS = ROOT / "docs"
WEB_DIST = ROOT / "web" / "dist"


class EconomicsIn(BaseModel):
    cost_to_fight_inr: float = Field(1800.0, ge=0)
    staff_cost_per_case_inr: float = Field(650.0, ge=0)
    goods_recovery_rate: float = Field(0.0, ge=0, le=1)
    pre_arb_reversal_rate: float = Field(0.20, ge=0, le=1)
    monthly_transactions: int = Field(42000, ge=0)
    tc40_count: int = Field(380, ge=0)
    tc15_count: int = Field(300, ge=0)
    usd_inr: float = Field(88.0, gt=0)

    def to_econ(self) -> CL.Economics:
        return CL.Economics(**self.model_dump())


@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_store()
    return {
        "status": "ok",
        "transactions": int(len(s.ledger)),
        "disputes": int(len(s.view)),
        "evidence_items": int(len(s.evidence)),
        "rules": registry.manifest()["active"],
        "evidence_log_backend": LOG.backend,
    }


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    return get_store().overview()


@app.get("/api/disputes")
def disputes(limit: int = Query(100, ge=1, le=500), only_104: bool = False) -> dict[str, Any]:
    return {"items": get_store().disputes(limit=limit, only_104=only_104)}


@app.get("/api/disputes/{dispute_id}")
def case(dispute_id: str) -> dict[str, Any]:
    s = get_store()
    if not s.has_dispute(dispute_id):
        raise HTTPException(404, f"unknown dispute {dispute_id}")
    # Deliberately NOT wrapped in `except KeyError`: a KeyError raised inside scoring is a
    # bug, and reporting it as "unknown dispute" hides it behind a plausible-looking 404.
    c = s.case(dispute_id)

    # Every material determination is written to the hash-chained log as it is made, so the
    # Section 63 certificate reflects what actually happened rather than a summary produced
    # after the fact.
    LOG.append("ce3_qualification", dispute_id, {
        "qualified": c["qualification"]["qualified"],
        "matched_elements": c["qualification"]["matched_elements"],
        "rule_version": c["qualification"]["rule_version"],
    })
    if c.get("evidence"):
        LOG.append("evidence_forensics", dispute_id, {
            "item_id": c["evidence"]["item_id"],
            "label": c["evidence"]["label"],
            "tamper_score": c["evidence"]["tamper_score"],
            "flags": [f["code"] for f in c["evidence"]["flags"]],
            "rule_version": c["evidence"]["rule_version"],
        })
    LOG.append("recommendation", dispute_id, {
        "action": c["recommendation"]["action"],
        "win_prob": c["win_prob"],
        "break_even": c["break_even"],
    })
    return c


@app.get("/api/evidence/{item_id}/image")
def evidence_image(item_id: str):
    s = get_store()
    v = s.evidence_verdict(item_id)
    if not v:
        raise HTTPException(404, f"unknown evidence item {item_id}")
    p = ROOT / v["path"]
    if not p.exists():
        raise HTTPException(404, "evidence file not bundled in this deployment")
    return FileResponse(p, media_type="image/jpeg")


@app.post("/api/evidence/analyze")
async def analyze_evidence(
    file: UploadFile = File(...),
    claimed_amount_inr: float | None = None,
    claimed_ts: str | None = None,
    claimed_descriptor: str | None = None,
) -> dict[str, Any]:
    """Run the full forensic pipeline on an uploaded file.

    This is detection only. Nothing here produces or modifies a document.
    """
    import tempfile

    import pandas as pd

    from aegis.sideb import forensics as FX
    from aegis.sideb import rules as ER

    s = get_store()
    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        feats = FX.extract(tmp_path, claimed_amount_inr, claimed_ts, claimed_descriptor)
        X = pd.DataFrame([{k: feats.get(k, 0.0) for k in s.sideb["features"]}])
        raw = s.sideb["model"].predict_proba(X)[:, 1][0]
        score = float(s.sideb["calibrator"].predict([raw])[0])
        flags = ER.evaluate(feats, claimed_amount_inr, claimed_ts)
        v = ER.verdict(score, flags)
        v["filename"] = file.filename
        v["features"] = {k: float(val) for k, val in feats.items()}
        v["top_features"] = _top(feats)
        entry = LOG.append("evidence_upload", f"upload:{file.filename}", {
            "label": v["label"], "tamper_score": v["tamper_score"],
            "flags": [f["code"] for f in v["flags"]],
            "file_sha256": _sha(tmp_path),
        })
        v["log_entry"] = {"seq": entry.seq, "entry_hash": entry.entry_hash}
        return v
    finally:
        tmp_path.unlink(missing_ok=True)


def _sha(p: Path) -> str:
    from aegis.evidence_log.chain import sha256_file

    return sha256_file(str(p))


def _top(feats: dict[str, float]) -> list[dict[str, Any]]:
    from aegis.api.store import _top_forensic_features

    return _top_forensic_features(feats)


@app.post("/api/costlab")
def costlab(econ_in: EconomicsIn) -> dict[str, Any]:
    """Sweep decision policies against the held-out test set, in rupees."""
    import numpy as np
    import pandas as pd

    from aegis.api.store import DATA

    econ = econ_in.to_econ()
    d = pd.read_parquet(DATA / "sidea_test_scored.parquet")
    a = d["amount_inr"].to_numpy()
    p = d["win_prob"].to_numpy()
    w = d["won_if_represented"].to_numpy()
    q = d["qualified"].to_numpy()

    out = CL.compare_with_rule_baseline(a, p, w, q, econ)
    out["expected_value"] = CL.sweep_ev(a, p, w, econ)
    out["vamp"] = CL.simulate_vamp(
        econ,
        tc40_challenges_won=int(d["tc40_reported"].sum() * 0.35),
        disputes_deflected=int(len(d) * 0.05),
    )
    out["test_set"] = {
        "n": int(len(d)),
        "exposure_inr": float(a.sum()),
        "actual_win_rate": float(w.mean()),
        "note": (
            "Every policy is scored on the same held-out disputes, from customers unseen "
            "during training. Thresholds are chosen on this set's economics, not its labels."
        ),
    }
    return out


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    import json

    out: dict[str, Any] = {}
    for name, fn in [("side_a", "metrics_sidea.json"), ("side_b", "metrics_sideb.json"),
                     ("fusion", "metrics_fusion.json")]:
        p = DOCS / fn
        if p.exists():
            out[name] = json.loads(p.read_text())
    out["rules"] = registry.manifest()
    return out


@app.get("/api/rules")
def rules() -> dict[str, Any]:
    from rules.ce3 import v2026_04 as ce3
    from rules.vamp import v2026_04 as vamp

    return {
        "manifest": registry.manifest(),
        "ce3": {
            "version": ce3.RULE_VERSION,
            "effective_from": ce3.EFFECTIVE_FROM.isoformat(),
            "eligible_reason_codes": sorted(ce3.ELIGIBLE_REASON_CODES),
            "prior_min_age_days": ce3.PRIOR_MIN_AGE_DAYS,
            "prior_max_age_days": ce3.PRIOR_MAX_AGE_DAYS,
            "min_prior_transactions": ce3.MIN_PRIOR_TRANSACTIONS,
            "main_elements": [{"key": e, "label": ce3.ELEMENT_LABELS[e]} for e in ce3.MAIN_ELEMENTS],
            "secondary_elements": [
                {"key": e, "label": ce3.ELEMENT_LABELS[e]} for e in ce3.SECONDARY_ELEMENTS
            ],
            "match_rule": "two Main elements, or one Main plus one Secondary",
            "common_misreading": (
                "Any two of the four data elements. This is wrong: two Secondary elements "
                "with no Main anchor do not qualify, and treating them as qualifying tells "
                "a merchant they can win a case they will certainly lose."
            ),
        },
        "vamp": {
            "version": vamp.RULE_VERSION,
            "effective_from": vamp.EFFECTIVE_FROM.isoformat(),
            "excessive_ratio": vamp.EXCESSIVE_RATIO,
            "monthly_item_floor": vamp.MONTHLY_ITEM_FLOOR,
            "fee_per_dispute_usd": vamp.FEE_PER_DISPUTE_USD,
            "regions": list(vamp.REGIONS),
            "numerator": "TC40 fraud reports + TC15 chargebacks (a fraud chargeback generates both)",
        },
    }


@app.get("/api/packet/{dispute_id}")
def packet(dispute_id: str) -> dict[str, Any]:
    """Assemble the CE 3.0 evidence bundle for human review. Never auto-submitted."""
    s = get_store()
    if not s.has_dispute(dispute_id):
        raise HTTPException(404, f"unknown dispute {dispute_id}")
    c = s.case(dispute_id)
    p = PB.build(c, LOG)
    LOG.append("packet_assembled", dispute_id, {
        "bundle_sha256": p["bundle_sha256"], "ready": p["ready_to_submit"],
    })
    p["certificate"] = LOG.certificate(dispute_id)
    return p


@app.get("/api/evidence-log/{dispute_id}")
def evidence_log(dispute_id: str) -> dict[str, Any]:
    return {
        "entries": LOG.entries(dispute_id),
        "chain": LOG.verify(),
        "certificate": LOG.certificate(dispute_id),
        "backend": LOG.backend,
    }


# --- static console -----------------------------------------------------------------

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Unknown API paths must 404 as JSON rather than silently returning the SPA shell,
        # which would turn a typo into a confusing parse error in the browser.
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        f = WEB_DIST / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(WEB_DIST / "index.html")
