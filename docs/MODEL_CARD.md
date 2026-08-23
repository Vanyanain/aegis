# AEGIS — Model Card

Covers three models and two rulebooks. Numbers live in [METRICS.md](METRICS.md), which is
generated from the artifacts; this document covers intent, data, limits and failure modes.

---

## 1. What these models are for

| Component | Type | Decides |
|---|---|---|
| CE 3.0 gate | **Rulebook** (`rules/ce3/`) | Whether a dispute meets the published Compelling Evidence 3.0 criteria |
| VAMP module | **Rulebook** (`rules/vamp/`) | Enforcement ratio, threshold, fee exposure |
| Side A win-prob | LightGBM + isotonic | Probability a representment succeeds |
| Side B forensics | LightGBM + isotonic | Probability submitted evidence is fabricated |
| Side B rules | **Rulebook** (`aegis/sideb/rules.py`) | Deterministic tamper flags |
| M3 intent | LightGBM multiclass, balanced | Criminal fraud / first-party misuse / service failure |

The two qualification-critical determinations are **rules, not models**. Whether a dispute
meets a published network criterion is not a probabilistic question, and a model that got it
approximately right would be worse than useless in a document filed with an issuer.

---

## 2. Data

**All synthetic. Disclosed everywhere it is reported.**

### 2.1 Transaction ledger (`synth/ledger.py`)

~122,000 transactions, 7,000 customers, 24 months, ~3,400 disputes.

Generated because no public fraud dataset carries device fingerprints, purchase IPs, account
IDs and shipping addresses **linked across a customer's order history** — which is precisely
and only what CE 3.0 qualification is computed over. Without it the rulebook cannot be
exercised at all.

The variable that matters is **data capture, not customer behaviour**. Coverage is partial,
channel-dependent and time-varying: device fingerprinting "switches on" partway through the
window, subscription rebills have no browser and often no client IP, guest checkouts never
get an account ID, services orders never have a shipping address. Two independent gates
apply — a field must be *captured*, then its value must be *stable* — so a captured-but-churned
mobile IP is present in the ledger and still fails to match. That is the failure mode a
coverage-only view of the problem would miss.

### 2.2 Evidence corpus (`synth/receipts/`)

3,000 Indian-format receipts (GSTIN, CGST/SGST, HSN codes). Genuine receipts are rendered
with thermal-printer jitter and ink variation, then pushed through a physical camera pipeline
— perspective warp, illumination gradient, paper fibre, defocus, sensor noise, phone JPEG.

Four fake families, each a **detection target**:

| Family | Construction | Primary signal | Ledger cross-check |
|---|---|---|---|
| `ai_generated` | Broken internal arithmetic, flawless typography; total pinned to the claim | Arithmetic | Consistent |
| `digital_edit` | Genuine photo, TOTAL repainted, re-saved | ELA / double compression | **Mismatch** |
| `template_forge` | Fixed template, correct arithmetic, identical geometry | Typography / layout | Consistent |
| `recycled` | Genuine, unaltered receipt from a different real order | *(none)* | **Mismatch** |

This layout is deliberate. Two families are ledger-consistent and two are not; two have
broken arithmetic and two do not. **No single feature can solve the problem**, which is the
only way the per-family and ablation numbers mean anything.

Family is drawn **conditional on the dispute's latent intent**: a cardholder whose card was
genuinely stolen has no reason to forge a receipt (81% genuine evidence), a customer whose
parcel never arrived usually has real proof (84% genuine), and it is first-party misuse that
needs a document and does not have one (27% genuine).

### 2.3 Two corpus defects found and fixed

Both were caught by diagnostics, not by the headline metric, and both would have produced
impressive and meaningless numbers:

1. **EXIF shortcut.** Initially every genuine receipt carried camera EXIF and every fake
   carried none. `exif_present` separated the classes perfectly and arithmetic, typography,
   compression and noise all scored **exactly zero** feature importance. That detector would
   have collapsed the first time a forger ran `exiftool`. Fixed by modelling delivery
   channels — WhatsApp strips metadata, screenshots have none, forgers fabricate it — so
   camera EXIF is now present at ~40% in *both* classes and the model was forced onto real
   document features.

2. **Merchant mismatch noise.** The receipt merchant was drawn at random rather than from the
   merchant that charged the card, so the merchant cross-check fired on ~35% of *genuine*
   receipts. That is a corpus defect masquerading as a noisy rule.

---

## 3. Circularity, and what bounds it

Outcome labels (`won_if_represented`) are drawn from a documented structural model in
`synth/ledger.py::draw_win_outcomes`. Training a model to recover labels a model produced is
**partly circular**, and no amount of methodology removes that.

Three things bound it:

1. **A per-issuer random effect** (`N(0, 0.60)`) enters the outcome draw and is **never**
   exposed as a feature. It is an irreducible confounder no model can invert.
2. **A large residual noise term** (`N(0, 0.55)`) on the logit.
3. **Customer-grouped splits everywhere.** No customer appears in two splits, in any model.
   `aegis/sidea/features.py::FORBIDDEN` enforces the leak list at build time rather than by
   convention, and point-in-time customer aggregates are computed strictly before each
   dispute date so no future order leaks into a past decision.

**What the metrics therefore mean:** whether the pipeline recovers a known structure under
noise. They are **not** external-validity claims. Coefficients were set so marginal win rates
land on published priors (15–20% standard evidence on 10.4, 40–60% once CE 3.0 qualifies),
but a prior chosen to match reality is not evidence about reality.

Validation on real dispute data is required before any of these numbers should be quoted as
a product claim.

---

## 4. Known limitations

- **`digital_edit` generalises poorly.** 30.8% recall when held out of training entirely. A
  small localised splice shares little with a wholly generated document or a recycled
  photograph. In-distribution it is caught at 95%+; against a genuinely novel editing
  technique, expect degradation.
- **OCR is load-bearing and imperfect.** Tesseract errors propagate directly into the
  arithmetic layer. Item amounts are the smallest type on the page and the least reliably
  read, so the item-sum check uses median outlier rejection and carries an
  `arith_items_reliable` flag; it is deliberately kept out of the human-facing verdict.
- **Rules-only recall is 46.7%.** The deterministic layer is high-precision (93.8%) and low
  recall by design. Its value is generalisation to unseen families and explainability, not
  aggregate score — combining it with the model adds ~0.3pt recall for ~2pt precision.
- **The intent classifier is weak on `criminal_fraud`** (P 0.264, R 0.453) even balanced.
  It is used as a distribution feeding a recommendation, never as a hard label.
- **Serving uses in-sample forensic scores.** The fusion model was *trained* on out-of-fold
  Side B scores so it never learned to rely on unrealistic sharpness, but at serving time the
  deployed forensic model scores items it trained on. This is unavoidable and correct — but
  it means the intent feature is slightly sharper in the demo than for genuinely new evidence.
- **VAMP inputs are merchant-supplied.** Transaction counts and TC40/TC15 volumes are entered,
  not observed. The FX rate is an explicit assumption surfaced in the UI.

---

## 5. Deliberate design decisions

**A false forgery accusation is the worst error this system can make.** It is made about a
real person on the strength of a photograph. So the verdict logic grades by how *robust* the
triggering evidence is, not how severe it sounds: a `critical` ledger-mismatch flag — arithmetic
on two numbers held directly — condemns on its own; an OCR-derived arithmetic flag needs the
model to corroborate, and otherwise drops to `REVIEW`. This cut false `TAMPERED` verdicts on
genuine receipts from ~10% to **1.2%** while keeping fake recall above 93% in every family.

**Thresholds are chosen in rupees.** Cost to fight is fixed per case while payoff scales with
the disputed amount, so a flat probability threshold is the wrong shape for the decision. The
break-even probability is per-case: `p > (c/amount + g) / (1 - reversal_rate)`.

**Representment wins do not reduce the VAMP ratio.** The TC15 already happened. Winning
recovers the money, not the ratio. `simulate_vamp` refuses to model it otherwise, because
that error would understate a merchant's enforcement exposure.

**Section 63 BSA, not Section 65B.** The Indian Evidence Act, 1872 was repealed by the
Bharatiya Sakshya Adhiniyam, 2023 (in force 1 July 2024). §63(4) requires *two* signatories.
Certificates are generated **unsigned** — the system records facts and hashes; the
attestations are human acts.

---

## 6. Defence-only posture

AEGIS acts only after a transaction, dispute or return exists. No component predicts how to
commit, evade or fabricate fraud. The forensic model detects tampering and cannot generate
it; the fake-receipt families are offline training targets under `synth/`, unreachable from
any API route. The Packet Builder never auto-submits — `ready_to_submit` is a statement about
completeness, not an instruction to file.

---

## 7. Reproducibility

Seed `20260822` throughout. Full regeneration:

```bash
python -m scripts.make_data && python -m scripts.make_receipts 3000 && python -m scripts.extract_evidence && python -m scripts.train_sidea && python -m scripts.train_sideb && python -m scripts.score_evidence_oof && python -m scripts.train_fusion && python -m scripts.write_metrics_doc
```
