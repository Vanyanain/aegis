# AEGIS

**Adjudication Evidence & Genuine-Intent Scoring** — a two-sided evidence-integrity engine
for friendly-fraud disputes.

Live: **https://aegis-895961483720.asia-south1.run.app** (Cloud Run, `asia-south1` / Mumbai)

Track: *AI Risk Manager — stop the merchant losing money to fraud, returns and chargebacks.*
Class of loss: **first-party (friendly) fraud chargebacks + return-evidence abuse.**
Posture: **strictly defence-only.**

---

## The idea

Two facts about the 2026 dispute market have never been connected into one tool.

**The only thing that reliably wins a friendly-fraud dispute is Visa Compelling Evidence 3.0,
and its gate is decided long before the dispute exists.** CE 3.0 needs two prior undisputed
transactions on the same credential, aged 120–364 days, matching the disputed order on
specific data elements. Most merchants discover at dispute time that their pipeline never
stored a device fingerprint. By then it is 120 days too late.

**The evidence layer itself is now under attack.** The share of flagged fake receipts that
are AI-generated went from 0% in March 2025 to 70.8% by mid-May 2026. When a customer
submits a "damaged item" photo or a fabricated receipt, no merchant dispute tool checks
whether it is real.

Incumbents auto-fight chargebacks and treat CE 3.0 as a submission checkbox. Document
forensics vendors serve internal expense fraud and never see a customer dispute. **Nothing
fights both directions of the evidence war** — and the two halves are not independent:

> A **recycled** receipt — genuine, unaltered, from a different real order — is
> forensically perfect. No pixel-level method will ever flag it. AEGIS catches it because it
> holds the transaction ledger and can check the amount, date and merchant against the
> actual charge. Owning both sides is what makes the detection possible.

---

## What it does

| Module | What it answers |
|---|---|
| **M1 · CE 3.0 qualifier** | Does this dispute qualify *now*? Which exact field is blocking it? What single capture change would flip it? |
| **M2 · Evidence forensics** | Is the customer's submitted proof real? Arithmetic, provenance, compression, typography, noise, and cross-check against the ledger. |
| **M3 · Genuine-intent** | Criminal fraud, first-party misuse, or a genuine service failure? This sets the *tone*, not just the decision. |
| **M4 · Cost Lab** | What is the rupee-optimal policy, given your economics and your VAMP position? |
| **M5 · Packet builder** | Assemble the CE 3.0 bundle + forensic report + §63 BSA certificate. **Never auto-submits.** |
| **M6 · Metrics** | The honest report, always reachable in the console. |

---

## Headline results

Full detail, generated from the model artifacts: **[docs/METRICS.md](docs/METRICS.md)**.

**Side B — evidence forensics** (583 held-out items, unseen customers):

| | AEGIS | Human baseline¹ |
|---|---|---|
| Recall | **97.3%** | 77.0% |
| False-positive rate | **3.4%** | 12.0% |
| Accuracy | **96.9%** | 79.7% |

¹ 30-annotator study, GPT4o-Receipt (arXiv 2603.11442).

Leave-one-family-out — trained on three fake families, tested on an unseen fourth:
`template_forge` 100%, `ai_generated` 92.7%, `recycled` 83.0%, **`digital_edit` 30.8%**. The
last one is the honest weak spot and is reported rather than averaged away.

**Side A — win probability**: ROC-AUC 0.745, PR-AUC 0.422 against a 19.7% base rate (≈2.1×
lift), Brier 0.146 → 0.136 after isotonic calibration.

**The rulebook finding.** 315 disputes worth **₹18.5 L** match two CE 3.0 data elements but
have no Main anchor. Visa rejects them; a naive "any two of four" reading calls them
winnable. See below.

**Cost Lab.** On the held-out set, the expected-value policy fights **60 cases** where
"fight everything that qualifies" fights **159** — and still loses **₹1.9 L less**.

---

## The rule most implementations get wrong

CE 3.0's four matchable elements are **not interchangeable**. They are tiered:

| Main | Secondary |
|---|---|
| Customer purchase IP | Shipping address |
| Device fingerprint **or** device ID *(one shared slot)* | Customer email address |
| | Customer account ID |

```
qualified  ⟺  two Main elements match across all three transactions
           ∨  one Main + one Secondary match across all three transactions
```

A naive "any two of four" reading admits `account_id + shipping_address` — two Secondary
elements with no Main anchor, which Visa does not accept. That false positive tells a
merchant they can win a case they will certainly lose, and bills them for the attempt.
`rules/ce3/v2026_04.py` implements the real rule and keeps the naive one only so the console
can show both verdicts side by side.

Sources for every rule constant: **[docs/RESEARCH.md](docs/RESEARCH.md)**, which also
corrects four factual errors in the original spec — including an RBI statistic whose real
driver is legacy loan fraud, not deepfakes, and a citation to a statute repealed in 2024.

---

## Architecture

```
                 ┌──────────────── AEGIS Console (React + TS) ───────────────┐
                 │ Overview · Disputes · Case · Cost Lab · Metrics · Rulebook │
                 └──────┬──────────────────────────────────────┬─────────────┘
                        │ REST/JSON (FastAPI)                  │
             ┌──────────▼──────────┐             ┌─────────────▼─────────────┐
             │ SIDE A              │             │ SIDE B                    │
             │ CE 3.0 rulebook     │             │ arithmetic · provenance   │
             │ + LightGBM win-prob │             │ ELA · typography · noise  │
             │ + isotonic + SHAP   │             │ + ledger cross-check      │
             │                     │             │ LightGBM + rule layer     │
             └──────────┬──────────┘             └─────────────┬─────────────┘
                        │                                       │
                 ┌──────▼────────── M3 fusion (intent) ─────────▼──────┐
                 │ recommended action + ₹ outcome + §63 BSA evidence log│
                 └──────────────────────┬──────────────────────────────┘
                                        ▼
                              M4 Cost Lab · M5 Packet
```

Both sides pair a **versioned rulebook** with a model. Rules give certainty and
explainability; models give graded suspicion. `rules/` carries effective dates, so a network
rule change is a one-file edit and nothing retrains — and a dispute is always judged under
the rulebook in force when it was raised.

---

## Running it

The ledger, dispute table, cached forensic features, trained models and a curated evidence
subset are all committed, so a clone runs without regenerating or retraining anything.

```bash
pip install -r requirements.txt && brew install tesseract
```

```bash
cd web && npm install && npm run build && cd .. && uvicorn aegis.api.main:app --port 8311
```

To regenerate everything from scratch instead (seed `20260822`, fully deterministic — this
also rebuilds the 123 MB training corpus, which is not committed):

```bash
python -m scripts.make_data && python -m scripts.make_receipts 3000 && python -m scripts.extract_evidence
```

```bash
python -m scripts.train_sidea && python -m scripts.train_sideb && python -m scripts.score_evidence_oof && python -m scripts.train_fusion && python -m scripts.write_metrics_doc
```

Deploy (Cloud Run builds the image remotely; no local Docker needed):

```bash
python -m scripts.prepare_deploy && gcloud run deploy aegis --source . --region=asia-south1 --allow-unauthenticated
```

---

## Honest caveats

- **All data is synthetic.** No public dataset carries the linked device/IP/account history
  CE 3.0 is computed over, so the ledger was generated; the fake-receipt corpus was
  generated too. Outcome labels come from a documented structural model, which makes the
  Side A metrics **partly circular**. Mitigations (hidden issuer effect, large noise term,
  customer-grouped splits) are described in [docs/MODEL_CARD.md](docs/MODEL_CARD.md).
  Real-data validation is future work, not a solved problem.
- **`digital_edit` generalises poorly** to unseen-family evaluation (30.8% recall). A small
  localised splice looks nothing like the other fake families.
- **OCR is a real dependency.** Tesseract errors propagate into the arithmetic layer; that
  is why an OCR-derived flag alone cannot condemn a document (see below).
- **The forensic verdict is deliberately conservative.** Accusing a customer of forgery is
  the most damaging thing this system can say. A `critical` ledger-mismatch flag condemns on
  its own; an OCR-derived arithmetic flag needs the model to corroborate, otherwise it drops
  to `REVIEW`. That change cut false tamper accusations on genuine receipts from ~10% to 1.2%.
- **The evidence log persists to Firestore** on Cloud Run and falls back to in-memory
  locally; the backend is reported in `/api/health` rather than assumed.

---

## Defence-only

AEGIS acts only **after** a transaction, dispute or return exists. It predicts defensibility,
verifies inbound evidence, and assembles defences for human review. No component predicts how
to commit, evade or fabricate fraud. The forensic model **detects** tampering and cannot
generate it — the fake-receipt families exist offline under `synth/` to train the detector and
are unreachable from any API route. The Packet Builder **never auto-submits**: it assembles a
bundle a human reviews and files.
