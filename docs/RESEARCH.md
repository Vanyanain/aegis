# AEGIS — Research Findings & PRD Corrections

Research pass date: 2026-08-22. Every claim below is sourced. Claims marked **CORRECTION**
contradict the draft PRD and must be fixed before pitching — each is the kind of detail a
payments-literate judge will test.

---

## 1. CORRECTION — the CE 3.0 matching rule in the PRD is wrong

**PRD said:** qualify on "≥2 of {device ID/fingerprint, IP address, account name, shipping address}".

**Actual rule** (Stripe's implementation of the Visa spec, the authoritative public statement):

Elements are split into two tiers, and they are **not interchangeable**:

| Main evidence elements | Secondary evidence elements |
|---|---|
| Customer purchase IP | Shipping address |
| Customer device fingerprint **or** customer device ID | Customer email address |
| | Customer Account ID |

Qualification requires the disputed transaction **and both** prior transactions to match on either:
- **two Main elements**, or
- **one Main + one Secondary element**.

Device fingerprint + device ID is explicitly **not** a valid pair — they occupy the same Main slot.

**Why this matters:** under the PRD's rule, `account name + shipping address` counts as a
qualifying pair. Under the real rule it qualifies for **nothing** — two Secondary elements with no
Main anchor. A naive implementation therefore produces **false "you can win this"** verdicts, which
is the single most expensive error this product could make. AEGIS's rules engine implements the
tiered rule, and the demo shows a case that the naive rule passes and the real rule rejects.

**Other hard gate conditions (full list):**
- Network reason code **10.4 only** (Other Fraud — Card-Absent).
- **≥2 prior transactions**, same payment credential, **undisputed and paid**, and **not** validation charges.
- Prior transactions aged **120–364 days** before the disputed transaction.
- Neither prior transaction may have been previously reported as fraud (TC40).
- `product_description` required on **all three** transactions.
- Disputed transaction must be categorised `merchandise` or `services`.

Sources: [Stripe — Visa CE 3.0 disputes](https://docs.stripe.com/disputes/api/visa-ce3),
[Chargebacks911](https://chargebacks911.com/prevent-chargebacks/prevent-visa-disputes/visa-compelling-evidence-3-0/),
[chargeback.io](https://www.chargeback.io/blog/the-ultimate-guide-to-visa-compelling-evidence-3-0)

---

## 2. NEW — the April 2026 TC40 expansion (a whole extra surface the PRD misses)

As of **18 April 2026**, CE 3.0 covers **non-disputed TC40 fraud reports** — cases where an issuer
files a fraud report that never becomes a chargeback. This matters enormously because:

- TC40s count toward the VAMP ratio **even though no chargeback exists**, and
- a single fraud chargeback generates **both** a TC40 and a TC15, effectively **double-counting**
  against the merchant.

Merchants can now challenge qualifying TC40s through Order Insight in Verifi. Nobody has
productised "which of my TC40s are CE 3.0-challengeable, and what does clearing them do to my
VAMP ratio?" — AEGIS's Cost Lab models exactly this.

Sources: [Verifi](https://www.verifi.com/in-the-news/compelling-evidence-3-0-ce3-0-in-the-pre-dispute-and-pre-arbitration-environments.html),
[cside VAMP 2026 playbook](https://cside.com/blog/vamp-2026-merchant-playbook)

---

## 3. CORRECTED & SHARPENED — VAMP 2026 economics

| Parameter | Value |
|---|---|
| Merchant "Excessive" threshold | **1.5%** (150 bps), effective **1 April 2026** (down from 2.2% at launch) |
| Fee at Excessive | **USD $8 per dispute** |
| Monthly floor | **1,500 items** — below this, a merchant is not identified regardless of ratio |
| Ratio numerator | **TC40 fraud reports + TC15 chargebacks combined** |
| Regions | US, Canada, EU, APAC |

The PRD's "₹-per-transaction penalty" is vague; the real figure is a flat **$8/dispute**, which is
what the Cost Lab must use (converted to ₹, with the FX rate shown as an explicit assumption).

Sources: [cside](https://cside.com/blog/vamp-2026-merchant-playbook),
[Chargeflow](https://www.chargeflow.io/blog/what-are-visas-new-vamp-rules-a-2025-guide-for-merchants),
[GivePayments](https://www.givepayments.com/resources/visa-vamp-2026/)

---

## 4. CORRECTION — the RBI ₹48,021 crore claim is real, but the PRD's *explanation* is false

**PRD said:** "RBI reported ₹48,021 crore of industry fraud in FY26 (+46.4% YoY), driven by cheap
open-source deepfake/document-tampering tools."

**What RBI actually reported for FY26:**
- Total fraud value **₹48,021 crore, +46.4% YoY** — ✅ correct.
- Number of cases **fell 57%** to 10,114.
- The rise is driven by the **advances/loans category**: 8,640 cases worth **₹40,774 crore**.
- RBI explicitly notes FY26 includes **314 legacy cases worth ₹30,199 crore** reclassified from
  prior years — this is the actual driver of the headline jump.
- **Digital payments fraud collapsed**: 293 cases worth **₹29 crore**, down from 13,332 cases
  (₹517 crore) in FY25. Digital's share of cases fell from 80.4% (FY24) to **2.9%** (FY26).

**Verdict: do not use this stat as a deepfake/payments-fraud hook.** It is a legacy-loan-fraud
number, and any judge who knows the report will call it out. India digital-payment fraud went
*down*. The honest India framing for AEGIS is **regulatory and evidentiary**, not volumetric:
Indian merchants need a dispute-evidence trail that is **admissible** (see §6), and India-first
merchants on Visa are subject to the same APAC VAMP 1.5% threshold from April 2026.

Sources: [Business Standard](https://www.business-standard.com/industry/banking/bank-fraud-cases-halve-but-value-climbs-to-48-000-crore-rbi-data-126052900631_1.html),
[Deccan Chronicle](https://www.deccanchronicle.com/nation/number-of-bank-frauds-fall-57-but-amount-rises-46-to-rs-48021-crore-in-fy26-1959980),
[Outlook Business](https://www.outlookbusiness.com/news/financial-institutions-report-over-10000-cases-of-fraud-involving-48000-cr-in-fy26-rbi-data)

---

## 5. UPGRADED — the AI-evidence threat is far stronger than the PRD claims

**PRD said:** ">20% of falsified evidence is AI-manipulated; AI fakes overtook template fakes in April 2026."

**Actual AppZen data (12 months ending 15 May 2026):**
- Share of detected fake receipts that are AI-generated rose from **0% (March 2025) → 70.8% (mid-May 2026)**.
- Template-based fakes fell from **95–100% → 29%** over the same period.
- 1,471 AI-generated receipts from 745 employees at 174 companies, worth $148,143 claimed.
- **Critical caveat to state openly:** 70.8% is a share of *flagged fraudulent* receipts — **not** a
  share of all submissions, and not an economy-wide fraud rate.

Self-reported behaviour:
- **34%** of 2,000 US/UK professionals admit to using AI to generate a fake business receipt (Emburse 2026).
- **32%** of finance professionals say they could not recognise an AI-generated fake receipt.

Sources: [PYMNTS](https://www.pymnts.com/news/artificial-intelligence/2026/ai-generated-fake-receipts-now-make-up-71percent-of-expense-fraud/),
[Accounting Today](https://www.accountingtoday.com/news/use-of-ai-receipts-in-expense-fraud-soars),
[Accounting Today — 32%](https://www.accountingtoday.com/news/32-admit-they-cannot-recognize-ai-generated-fake-receipts),
[Emburse](https://www.emburse.com/blog/ai-generated-receipts-expense-fraud),
[Forbes](https://www.forbes.com/sites/jamesbroughel/2026/06/28/ai-generated-fake-receipts-are-changing-expense-fraud/)

### 5a. A real, citable human baseline exists — and a dominant forensic signal

The **GPT4o-Receipt** benchmark (arXiv 2603.11442) is directly on-point: 1,235 receipts (935
GPT-4o-generated, 300 authentic), 159 merchant categories, with a 30-annotator human study.

**Human performance detecting AI receipts:**

| Metric | Value | 95% CI |
|---|---|---|
| Accuracy | **0.797** | 0.774–0.818 |
| F1 | 0.852 | 0.833–0.869 |
| **Recall** | **0.770** | 0.743–0.797 |
| FPR | 0.120 | 0.085–0.160 |

This replaces the PRD's hand-waved "~68% human baseline" with a **defensible, peer-visible number**.
AEGIS Side B must beat **0.770 recall at ≤0.120 FPR** to make its claim.

**The dominant signal — and it is not pixels:** arithmetic errors appear in **97.2%** of AI-generated
receipts. Image generators treat numbers as visual tokens, so line items don't sum to the subtotal
and tax doesn't reconcile. Humans have *better* visual discrimination than most machine evaluators
but still lose, because arithmetic inconsistency is **imperceptible to visual inspection**.

**Design consequence:** Side B leads with **arithmetic/tax-math integrity**, not a CNN. That is
also the most explainable possible evidence — "line items sum to ₹4,180 but the stated subtotal is
₹4,240" is a sentence a judge, an issuer, and an Indian court can all read. The CNN becomes a
secondary residual signal, not the headline.

Source: [GPT4o-Receipt, arXiv 2603.11442](https://arxiv.org/html/2603.11442v2)

---

## 6. CORRECTION — "Section 65B" is the wrong statute to cite in 2026

The Indian Evidence Act, 1872 was **repealed and replaced by the Bharatiya Sakshya Adhiniyam
(BSA), 2023, in force from 1 July 2024**. Electronic-record admissibility is now governed by
**Section 63 BSA**, which carries forward the four conditions and the certificate requirement of
the old §65B.

Key change: **Section 63(4) BSA moves from a single-signatory to a dual-signatory certificate** —
signed both by the person in a responsible official position over the device *and* by an expert,
where expert examination was undertaken.

**Design consequence:** AEGIS's evidence log must be shaped for **§63(4) BSA** with a
dual-signature block (system-custodian + forensic-expert attestation), citing §65B only as the
legacy name. Getting this right is a genuine India-first differentiator; getting it wrong reads as
copied boilerplate.

Sources: [Lexology](https://www.lexology.com/library/detail.aspx?g=0b260518-486d-4b02-b73c-7540b7565dab),
[Bar & Bench](https://www.barandbench.com/columns/navigating-the-transition-implications-of-the-bhartiya-sakshya-adhiniyam-on-digital-evidence-in-ongoing-trials),
[The Law Codes](https://thelawcodes.com/article/the-certificate-under-section-65b-of-the-indian-evidence-act-and-section-634-c-of-the-bhartiya-sakshya-adhiniyam-2023/)

---

## 7. Market sizing — use ranges, not the PRD's single number

Friendly-fraud share of chargebacks is **genuinely contested** and varies by scope. Report the
range, not one figure:

| Claim | Value | Scope |
|---|---|---|
| Friendly fraud share of all chargebacks | **≥22%** globally (2026) | all merchants, all verticals |
| Friendly fraud share of *ecommerce disputes* | **~75%** of cases | ecommerce only |
| Digital-goods merchants | **up to 80%** of fraud cases are first-party misuse | narrow vertical |
| Annual merchant cost | **$132B/yr** | global |
| Transaction value lost | **$8.1B (2026) → ~$16B (2031)**, +96% | Juniper |
| All-in cost per $1 of chargeback | **$3.75–$4.61** (+37% since 2021) | — |
| Overall representment win rate | **~41%** (Adaptiv 2026); 54% US (Chargebacks911) | — |
| **Net recovery after 2nd chargebacks & costs** | **only 12–18%** | the number that matters |

That last row is the strongest line in the whole deck: **the headline win rate overstates dollars
actually recovered by roughly 3×.** It is precisely why a *pre-dispute qualification* product beats
a *post-dispute auto-fighter* — and it justifies AEGIS's cost-first, ₹-denominated framing.

Sources: [Chargeflow](https://www.chargeflow.io/blog/chargeback-statistics-trends-costs-solutions),
[chargeback.io](https://www.chargeback.io/blog/chargeback-statistics),
[Juniper Research](https://www.juniperresearch.com/press/friendly-fraud-to-make-up-28-percent-of-chargebacks-globally/),
[justpricing](https://justpricing.com/chargeback-statistics)

---

## 8. Competitive gap — confirmed, and sharper than the PRD stated

- **Chargeflow / Justt / Signifyd** — post-dispute automated representment. CE 3.0 treated as a
  submission checkbox, not a *predictive qualification* problem. No inbound-evidence verification.
- **AppZen / Resistant.ai** — genuine document forensics, but scoped to **internal expense fraud**.
  Their subject is an employee's expense claim, never a customer's dispute evidence.
- **Verifi Order Insight / RDR** — network-side pre-dispute deflection, but merchant sees no
  *predictive readiness* view and no forensic check.

**The structural moat, stated precisely:** AppZen cannot detect a *recycled genuine receipt* —
a real, unaltered receipt from a different transaction submitted as proof for this dispute. No
pixel forensic will ever flag it, because nothing about the image is fake. AEGIS catches it
trivially, because AEGIS holds the transaction ledger and can cross-check amount, timestamp,
descriptor and line items against the actual order. **Owning both sides is what makes the
detection possible** — that is the product thesis, not a feature list.

---

## 9. Net effect on the build

| # | Change from PRD |
|---|---|
| 1 | Rules engine implements the **tiered Main/Secondary** CE 3.0 match, not "any 2 of 4". Demo contrasts both. |
| 2 | Add **TC40 challenge** surface + VAMP double-count modelling (April 2026 rule). |
| 3 | Cost Lab uses **$8/dispute, 1.5%, 1,500-item floor**, FX shown as an assumption. |
| 4 | **Drop** the RBI-deepfake causal claim. Reframe India angle as **evidentiary/regulatory**. |
| 5 | Side B leads with **arithmetic integrity** (97.2% signal), CNN demoted to residual. |
| 6 | Target and report against the real human baseline: **0.770 recall @ 0.120 FPR**. |
| 7 | Evidence log shaped for **§63(4) BSA 2023 dual-signatory**, not §65B single. |
| 8 | Headline the **12–18% net recovery** stat to justify pre-dispute positioning. |
| 9 | Add a **recycled-genuine-receipt** fake family — the class only AEGIS can catch. |
