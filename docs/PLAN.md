# AEGIS — Implementation Plan

Two-sided evidence-integrity engine for friendly-fraud disputes.
Companion doc: [RESEARCH.md](RESEARCH.md) — corrections to the PRD, all sourced.

---

## 0. Stack & repo layout

Everything needed is already installed locally (numpy 2.4, pandas 3.0, sklearn 1.8, lightgbm 4.6,
torch 2.13, opencv 4.13, Pillow 11.3, shap 0.51, fastapi, uvicorn). No Docker locally — Cloud Run
`--source` deploys build remotely via Cloud Build.

```
AEGIS/
├── docs/                    RESEARCH.md, PLAN.md, METRICS.md, MODEL_CARD.md
├── rules/                   versioned rulebook — NOT model weights
│   ├── ce3/v2026_04.py      CE 3.0 gate (tiered Main/Secondary)
│   ├── vamp/v2026_04.py     1.5% / $8 / 1,500-floor / TC40+TC15
│   └── registry.py          version resolution + effective-date lookup
├── synth/                   data generation (offline, reproducible, seeded)
│   ├── ledger.py            CE3.0-shaped transaction ledger
│   ├── receipts/            genuine renderer + 4 fake families
│   └── cards/               dataset cards, disclosed synthetic
├── aegis/
│   ├── sidea/               qualifier: gate + win-prob GBM + SHAP + gap counterfactuals
│   ├── sideb/               forensics: arithmetic, metadata, ELA, copy-move, typography, CNN
│   ├── fusion/              intent meta-model → recommended action
│   ├── costlab/             ₹ loss curve, threshold sweep, VAMP simulator
│   ├── packet/              CE3.0 bundle + §63(4) BSA certificate + hash chain
│   ├── evidence_log/        append-only hash-chained audit trail
│   └── api/                 FastAPI app
├── web/                     React + Vite + TS console
├── models/                  trained artifacts (joblib / onnx)
├── data/                    generated ledger + receipt corpus (gitignored, regenerable)
├── Dockerfile               multi-stage: node build → python serve
└── Makefile                 make data | train | eval | dev | deploy
```

**Hard rule:** the rulebook lives in `rules/` as versioned Python with effective dates, never baked
into model weights. When Visa moves the threshold again, one file changes.

---

## Phase 1 — Synthetic CE 3.0 ledger  *(~5h)*

No public fraud dataset carries device fingerprints, IPs and multi-order customer histories, so
qualification logic is untestable on real open data. We synthesise, and **disclose it loudly**.

**Scale:** ~4,000 customers, ~60,000 transactions over 24 months, ~2,400 disputes.

**Per transaction:** `txn_id, customer_id, card_token, ts, amount_inr, mcc, merchandise_or_services,
product_description, descriptor_text, channel, avs_match, cvv_match, threeds_status,
delivery_confirmed` plus the CE 3.0 capture fields — `purchase_ip, device_fingerprint, device_id,
customer_account_id, customer_email, shipping_address_hash`.

**The realism that makes this worth building:** capture coverage is *deliberately incomplete and
time-varying*.
- Device fingerprint only present on web checkout, and only after the merchant "enabled" it partway
  through the window.
- IP missing on ~30% of in-app transactions.
- Shipping address only exists for `merchandise`, never `services`.
- Email present nearly always; account ID only for logged-in orders.

This is the entire product thesis made testable: most merchants fail CE 3.0 on **data capture**,
not on customer behaviour.

**Dispute generation:** each dispute draws a latent intent ∈ {`criminal_fraud`,
`first_party_misuse`, `genuine_service_failure`} with reason code assigned conditionally
(10.4 for fraud claims, 13.1 not-received, 13.3 not-as-described). `won_if_represented` is drawn
from a documented structural model: CE 3.0 qualification gives a large lift (priors anchored to
published 15–20% standard vs 40–60% CE 3.0 win rates), modulated by intent, evidence strength and
an **unobserved issuer effect the model never sees**.

**Honesty guard (this matters for "THE BAR"):** labels come from a generative model, so training a
model to recover them is partly circular. Mitigations, all stated in `MODEL_CARD.md`:
1. Substantial label noise + a hidden issuer confounder excluded from features.
2. **Group split by `customer_id`**, never by row — no customer appears in both train and test.
3. Explicit framing: *these metrics measure whether the pipeline recovers a known structure under
   noise. They are not external-validity claims.* Real-data validation is stated as future work.

**Deliverable:** `data/ledger.parquet`, `data/disputes.parquet`, `synth/cards/ledger_card.md`.

---

## Phase 2 — Side A: CE 3.0 qualifier  *(~7h)*

### 2a. Rules engine (`rules/ce3/v2026_04.py`)

Implements the **real** tiered rule (see RESEARCH.md §1):

```
MAIN      = {purchase_ip, device_fp_or_id}      # fp and id share ONE slot
SECONDARY = {shipping_address, email, account_id}

qualified ⟺ (2 MAIN match across all 3 txns)
          ∨ (1 MAIN + 1 SECONDARY match across all 3 txns)
```

Plus every hard gate: RC 10.4 only · ≥2 priors on the same credential · priors paid, undisputed,
not validation charges · aged **120–364 days** · neither prior TC40-reported · `product_description`
on all three · `merchandise_or_services` set.

Returns `QualificationResult{qualified, matched_elements, best_prior_pair, blocking_gaps[],
remediation[], rule_version}`.

**Demo moment:** ship a case that the PRD's naive "any 2 of 4" rule marks *winnable* and the real
tiered rule rejects (account ID + shipping address — two Secondaries, no Main anchor). Shows the
rulebook is real, not decorative.

### 2b. Gap diagnosis — the counterfactual nobody ships

For every near-miss, compute: **which single field, if it had been captured, flips this to
qualified?** Output is merchant-actionable — "enable device fingerprinting at web checkout" — not a
score.

### 2c. Portfolio Capture-Readiness (the pre-dispute headline)

Across the whole ledger: what fraction of *future* 10.4 disputes would qualify under current
capture coverage? And the time-lagged projection — *"enable device fingerprinting today and in 120
days X% of your 10.4 disputes become defensible."* The 120-day rule means today's config determines
next quarter's win rate. That is the argument for buying this **before** a dispute exists.

### 2d. Win-probability model

LightGBM over qualification state, matched-element identity/count, customer tenure, prior order
count, amount, refund history, descriptor-clarity score, dispute velocity, category, delivery
confirmation, AVS/CVV, 3DS. **Isotonic calibration** on a held-out calibration split. SHAP reason
codes per case. Grouped split by customer.

---

## Phase 3 — Side B: inbound evidence forensics  *(~10h)*

### 3a. Corpus

**Genuine:** programmatically rendered receipts (varied templates, fonts, thermal-printer jitter),
then pushed through a realistic **camera pipeline** — perspective warp, paper texture, lighting
gradient, sensor noise, phone-grade JPEG — with plausible EXIF (make/model/timestamp).

**Four fake families**, each a *detection target*:

| Family | Construction | Primary detectable signal |
|---|---|---|
| `ai_generated` | synthesised with internally inconsistent arithmetic, flawless typography, no camera EXIF, single-pass JPEG | **arithmetic break** (the 97.2% signal) |
| `digital_edit` | genuine receipt, total region spliced/patched, re-saved | ELA + double-quantisation |
| `template_forge` | fixed generator template, identical kerning/layout grid | template hash + kerning entropy |
| `recycled` | a **genuine, unaltered** receipt from a *different real transaction* | **ledger cross-check only** |

`recycled` is the strategic one: pixel-perfect, forensically clean, invisible to AppZen and every
expense-audit tool — and trivially caught by AEGIS because AEGIS holds the transaction record.
It is the concrete proof that owning both sides enables detection neither side can do alone.

### 3b. Features — explainable first, CNN last

1. **Arithmetic integrity** *(primary)* — line items vs subtotal, tax reconciliation, rounding
   convention. Leads because it is the dominant real signal *and* the most human-readable.
2. **Metadata/provenance** — camera make/model, software tag, C2PA, EXIF timestamp vs transaction
   date, GPS presence.
3. **ELA** — error-level statistics, text regions vs background.
4. **JPEG forensics** — quantisation-table fingerprint, double-compression detection.
5. **Copy-move** — ORB/block-matching duplicate-region detection.
6. **Typography** — glyph bbox variance, baseline drift, stroke-width consistency, kerning entropy
   (real thermal print jitters; generated text is too regular).
7. **Ledger cross-consistency** — amount, date-in-window, descriptor, currency, line-item
   plausibility vs the actual order.

**Model:** LightGBM over ~40 classical features (primary, fully explainable) + a small CNN on the
image residual, stacked by logistic regression. Calibrated authenticity score + tamper-type codes.

**OCR:** use Tesseract if available; otherwise the prototype consumes a noise-injected OCR
simulation over rendered ground truth, with the substitution stated plainly in the model card. Real
OCR is a drop-in — it is not the contribution.

### 3c. Evaluation — the part that separates this from a hackathon toy

- Standard held-out precision/recall/F1/PR-AUC/ROC-AUC/Brier/calibration.
- **Per-family recall** — generalisation is not one number.
- **Leave-one-family-out**: train on 3 fake families, test on the unseen 4th. This is the honest
  answer to "will it catch a generator you've never seen?"
- **Benchmarked against the real human baseline: 0.770 recall @ 0.120 FPR**
  ([GPT4o-Receipt](https://arxiv.org/html/2603.11442v2), 30 annotators). Beating it is the claim;
  if a family loses to it, that gets reported too.

---

## Phase 4 — Fusion, evidence log, TC40  *(~6h)*

**M3 Genuine-Intent:** logistic meta-model over Side A qualification, Side B authenticity, and
behavioural signals (post-purchase usage, descriptor-recognition risk, dispute velocity,
cancel-then-dispute, delivery confirmation, refund history). Outputs a distribution over
{criminal_fraud, first_party_misuse, genuine_service_failure} → recommended action:
`ACCEPT_LOSS · SOFT_REFUND · REPRESENT_STANDARD · REPRESENT_CE3 · ESCALATE_FORENSIC`.
Intent determines **tone**: a confused customer gets a soft refund, an abuser gets a firm representment.

**Evidence log:** append-only, SHA-256 hash-chained. Every artifact, feature value and verdict is
hashed in order with timestamps. Shaped for **§63(4) BSA 2023** with a **dual-signature** block
(system custodian + forensic expert), citing §65B as the legacy name. See RESEARCH.md §6.

**TC40 surface (April 2026 rule):** flag non-disputed TC40s that are CE 3.0-challengeable, and feed
the count into the VAMP simulator — clearing a TC40 removes ratio numerator without any chargeback
ever existing.

---

## Phase 5 — Cost Lab  *(~5h)*

Merchant inputs: avg dispute value ₹, cost to fight ₹, COGS, monthly transaction count, TC40 count,
TC15 count, staff cost. Rulebook constants: **1.5% threshold, $8/dispute at Excessive, 1,500-item
floor**, FX rate surfaced as an explicit assumption.

Outputs:
- Threshold sweep over t ∈ [0,1] → **total ₹ loss curve**, cost-optimal point marked.
- Benchmarks: **fight-all**, **fight-none**, **fight-if-qualified**, **AEGIS-optimal**.
- **Confusion matrix denominated in ₹**, not counts — FP cost stated in rupees.
- **VAMP simulator**: ratio under each policy against the 1.5% line, including TC40+TC15
  double-counting and the effect of TC40 challenges.

The threshold is chosen by **cost, not accuracy**. That is the track's stated bar.

---

## Phase 6 — Console  *(~8h)*

React + Vite + TypeScript. Dispute war-room, not a SaaS landing page.

Dark slate canvas `#0B0F14` · **amber** `#F5A524` at-risk/unqualified · **teal** `#14B8A6`
defensible/verified · **red** `#EF4444` tampered. JetBrains Mono for every numeral, Inter for prose.
Dense, high-information, deliberately not the cream-serif-terracotta AI default.

Routes:
1. **Overview** — portfolio capture-readiness, VAMP gauge vs 1.5%, ₹ at risk.
2. **Disputes** — queue, sortable by ₹ exposure × win probability.
3. **Case** — the two-column split that *is* the product: Side A qualification ladder left, Side B
   forensic verdict right, fusion recommendation across the bottom.
4. **Cost Lab** — interactive sweep, live ₹ curve.
5. **Metrics** — the full honest report, always reachable.
6. **Packet** — assembled CE 3.0 bundle, review + export. **Never auto-submits.**

---

## Phase 7 — GCP deploy  *(~3h)*

Single **Cloud Run** service, region **asia-south1 (Mumbai)** — India-first product, correct
latency story for the pitch.

- Multi-stage Dockerfile: node builds the React bundle → python image serves FastAPI + static.
- `gcloud run deploy --source .` → Cloud Build does the image build remotely (no local Docker).
- Models baked into the image as joblib/ONNX — no cold-start training, no model registry needed.
- `torch.set_num_threads(1)` at import (known issue in this miniforge environment).
- Secrets via Secret Manager if any; none required for the demo.
- Artifact Registry auto-provisioned by the source deploy.

---

## Deliverables at the end

| Artifact | What it proves |
|---|---|
| Live Cloud Run URL | it runs |
| `docs/METRICS.md` | precision/recall/F1/PR-AUC/Brier, both sides, ₹ confusion matrix, LOFO, per-family recall, human baseline comparison |
| `docs/MODEL_CARD.md` | synthetic-data disclosure, circularity mitigations, known limits |
| `docs/RESEARCH.md` | every claim sourced; PRD errors corrected |
| `rules/` | the rulebook as versioned code with effective dates |
| Exported packet (PDF+JSON) | CE 3.0 bundle + §63(4) BSA certificate + hash chain |

---

## Defense-only posture

AEGIS acts only **after** a transaction, dispute or return exists. It predicts defensibility,
verifies inbound evidence, and assembles defences for merchant review. No component predicts how to
commit, evade or fabricate fraud. The forensics model **detects** fakes and cannot generate them —
the synthetic fake corpus is standard doctored-document simulation for detector training, generated
offline and never exposed through the API. The Packet Builder **never auto-submits**; a human
reviews and exports.

---

## Scope discipline

**MVP (must ship):** M1 qualifier · M2 forensics · M4 Cost Lab · M6 Metrics.
**Stretch (ship if time):** M3 fusion intent · M5 packet builder · TC40 surface · abuse-ring linkage.

Risks: synthetic-data external validity → disclosed, framed as methodology + working prototype ·
forensics generalisation → classical features kept alongside the CNN, LOFO reported · rulebook
drift → versioned module, not weights.
