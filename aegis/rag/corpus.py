"""The rulebook corpus AEGIS retrieves over.

Every passage carries a citation, because the whole product is about evidence and an
uncited assertion about a network rule is worth nothing. Passages are written from the
sources recorded in docs/RESEARCH.md, and the `source` field is what gets shown to the user
alongside any answer.

This corpus is deliberately small and hand-curated rather than scraped. Fifty accurate
passages that a merchant can act on beat ten thousand scraped ones that might contain a
2019 version of a threshold that changed in April 2026 -- and in this domain a stale
threshold is not a stale fact, it is a wrong decision about money.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    id: str
    topic: str
    text: str
    source: str
    url: str = ""


CORPUS: list[Passage] = [
    # --- CE 3.0 core criteria -----------------------------------------------------
    Passage(
        "ce3.tier_rule", "CE 3.0 matching rule",
        "Compelling Evidence 3.0 splits its matchable data elements into two tiers that are "
        "NOT interchangeable. Main elements are the customer purchase IP address, and the "
        "customer device fingerprint or device ID (which share a single slot). Secondary "
        "elements are the shipping address, the customer email address, and the customer "
        "account ID. A dispute qualifies only if the disputed transaction and both prior "
        "transactions match on either two Main elements, or one Main element plus one "
        "Secondary element. Two Secondary elements with no Main anchor never qualify.",
        "Stripe, Visa Compelling Evidence 3.0 disputes",
        "https://docs.stripe.com/disputes/api/visa-ce3",
    ),
    Passage(
        "ce3.naive_misreading", "Common CE 3.0 misreading",
        "A widespread misreading treats CE 3.0 as requiring 'any two of four' data elements. "
        "This is wrong and expensive: under that reading a customer account ID plus a "
        "shipping address appears to qualify, when Visa rejects it for having no Main "
        "anchor. A tool applying the naive rule tells a merchant they can win a case they "
        "will certainly lose, and bills them the representment cost for it.",
        "docs/RESEARCH.md section 1",
    ),
    Passage(
        "ce3.prior_window", "Prior transaction window",
        "CE 3.0 requires at least two previous transactions on the same payment credential "
        "that were not disputed. Those priors must be aged between 120 and 364 days relative "
        "to the disputed transaction. Because the floor is 120 days, the data a merchant "
        "starts capturing today first affects qualification roughly four months later -- "
        "qualification is decided long before the dispute exists.",
        "Stripe, Visa Compelling Evidence 3.0 disputes",
        "https://docs.stripe.com/disputes/api/visa-ce3",
    ),
    Passage(
        "ce3.prior_conditions", "Prior transaction eligibility",
        "Prior transactions used for CE 3.0 must be paid and settled, must never have been "
        "disputed, must not have been reported as fraud under a TC40, and must not be "
        "validation charges (zero-amount authorisations). All three transactions -- the "
        "disputed one and both priors -- must additionally carry a product description, and "
        "the disputed transaction must be categorised as either merchandise or services.",
        "Stripe, Visa Compelling Evidence 3.0 disputes",
        "https://docs.stripe.com/disputes/api/visa-ce3",
    ),
    Passage(
        "ce3.reason_code", "Which disputes are eligible",
        "Only Visa network reason code 10.4, Other Fraud - Card-Absent Environment, is "
        "eligible for Compelling Evidence 3.0. Disputes under 13.1 (merchandise or services "
        "not received) and 13.3 (not as described or defective) need different evidence "
        "entirely: delivery confirmation for 13.1, and the product description as displayed "
        "at purchase for 13.3.",
        "Chargebacks911, Visa CE 3.0 guide",
        "https://chargebacks911.com/prevent-chargebacks/prevent-visa-disputes/visa-compelling-evidence-3-0/",
    ),
    Passage(
        "ce3.tc40_expansion", "April 2026 TC40 expansion",
        "As of 18 April 2026, CE 3.0 covers non-disputed TC40 fraud reports -- cases where an "
        "issuer files a fraud report that never becomes a chargeback. Those TC40s count "
        "toward the VAMP ratio even though no chargeback exists, and qualifying ones can now "
        "be challenged through Order Insight in Verifi. Clearing a TC40 removes ratio "
        "numerator without any chargeback having occurred.",
        "Verifi / cside VAMP 2026 playbook",
        "https://cside.com/blog/vamp-2026-merchant-playbook",
    ),
    Passage(
        "ce3.win_rates", "What CE 3.0 is worth",
        "Standard evidence wins roughly 15 to 20 percent of friendly-fraud disputes. CE 3.0 "
        "raises that to roughly 40 to 60 percent when a case qualifies. But headline win "
        "rates overstate recovered money: net recovery after second chargebacks, "
        "pre-arbitration reversals and the cost of fighting is only 12 to 18 percent, so the "
        "headline figure overstates actual dollars by roughly three times.",
        "Chargeflow / Adaptiv 2026 chargeback statistics",
        "https://www.chargeflow.io/blog/chargeback-statistics-trends-costs-solutions",
    ),

    # --- VAMP ----------------------------------------------------------------------
    Passage(
        "vamp.threshold", "VAMP excessive threshold",
        "From 1 April 2026 the Visa Acquirer Monitoring Program merchant 'excessive' "
        "threshold is 1.5 percent, or 150 basis points, tightened from 2.2 percent at "
        "launch. It applies across the US, Canada, EU and APAC. Merchants at the excessive "
        "level pay a fee of USD 8 per dispute.",
        "cside, VAMP 2026 merchant playbook",
        "https://cside.com/blog/vamp-2026-merchant-playbook",
    ),
    Passage(
        "vamp.numerator", "How the VAMP ratio is computed",
        "The VAMP ratio combines TC40 fraud reports and TC15 chargebacks in a single "
        "numerator. A single fraud chargeback normally generates both, so it is effectively "
        "double-counted. Computing the ratio as chargebacks divided by transactions "
        "understates the real position by roughly half on the fraud portion.",
        "cside / Chargeflow VAMP 2026",
        "https://cside.com/blog/vamp-2026-merchant-playbook",
    ),
    Passage(
        "vamp.floor", "VAMP monthly item floor",
        "Below 1,500 monthly items a merchant is not identified under VAMP at all, however "
        "high the ratio runs. Acquirers commonly enforce stricter internal limits, but the "
        "network programme itself does not engage below the floor. Small merchants often "
        "worry about a ratio that cannot be enforced against them.",
        "cside, VAMP 2026 merchant playbook",
        "https://cside.com/blog/vamp-2026-merchant-playbook",
    ),
    Passage(
        "vamp.representment", "Representment does not fix the ratio",
        "Winning a representment recovers the money but does not remove the chargeback from "
        "the VAMP ratio -- the TC15 already occurred. Only pre-dispute deflection (Order "
        "Insight, RDR) and successful TC40 challenges reduce the numerator. Modelling "
        "representment wins as ratio relief is a common and expensive error.",
        "docs/RESEARCH.md sections 2-3",
    ),

    # --- Evidence integrity ---------------------------------------------------------
    Passage(
        "evidence.ai_receipts", "AI-generated receipts",
        "The share of flagged fake receipts identified as AI-generated rose from 0 percent in "
        "March 2025 to 70.8 percent by mid-May 2026, while template-based fakes fell from "
        "95-100 percent to 29 percent. That figure is a share of receipts already flagged as "
        "fraudulent -- not a share of all submissions, and not an economy-wide fraud rate.",
        "AppZen data via PYMNTS / Accounting Today",
        "https://www.pymnts.com/news/artificial-intelligence/2026/ai-generated-fake-receipts-now-make-up-71percent-of-expense-fraud/",
    ),
    Passage(
        "evidence.arithmetic", "Why arithmetic is the dominant signal",
        "Image generators treat numerals as visual tokens rather than quantities, so a "
        "generated receipt commonly looks plausible while failing to reconcile. Arithmetic "
        "errors appear in 97.2 percent of AI-generated receipts. Humans have better visual "
        "discrimination than most machine evaluators and still lose, because an arithmetic "
        "inconsistency is invisible to visual inspection.",
        "GPT4o-Receipt benchmark, arXiv 2603.11442",
        "https://arxiv.org/html/2603.11442v2",
    ),
    Passage(
        "evidence.human_baseline", "Human detection baseline",
        "In a 30-annotator study on AI-generated receipts, humans achieved 0.797 accuracy, "
        "0.770 recall and a 0.120 false-positive rate. Separately, 32 percent of finance "
        "professionals say they could not recognise an AI-generated fake receipt at all.",
        "GPT4o-Receipt, arXiv 2603.11442; Accounting Today",
        "https://arxiv.org/html/2603.11442v2",
    ),
    Passage(
        "evidence.real_receipts", "Real receipts break naive arithmetic checks",
        "Genuine receipts do not satisfy 'subtotal plus tax equals total'. They carry service "
        "charges, rounding adjustments and discounts on separate lines. Measured on real CORD "
        "receipts, ignoring the service charge produced apparent 4 to 6 percent errors on "
        "perfectly genuine restaurant bills. A reliable check that needs no tax line is that "
        "the total can never be below the subtotal, and cannot exceed it by more than a "
        "plausible tax-plus-service ceiling.",
        "AEGIS measurement on CORD (naver-clova-ix/cord-v2)",
    ),
    Passage(
        "evidence.recycled", "The recycled-receipt class",
        "A recycled receipt is genuine, unaltered, and belongs to a different real order. No "
        "pixel-level forensic method can flag it, because nothing about the image is fake. It "
        "is caught only by cross-checking the document against the transaction record -- "
        "amount, date, merchant. This is the class that requires owning both the evidence and "
        "the ledger, and it is why document-forensics vendors scoped to expense fraud cannot "
        "detect it.",
        "docs/RESEARCH.md section 8",
    ),

    # --- Indian admissibility --------------------------------------------------------
    Passage(
        "india.section63", "Section 63 BSA, not Section 65B",
        "The Indian Evidence Act, 1872 was repealed by the Bharatiya Sakshya Adhiniyam, 2023, "
        "in force from 1 July 2024. Electronic-record admissibility is now governed by "
        "Section 63 BSA, which carries forward the four conditions and the certificate "
        "requirement of the former Section 65B. Citing Section 65B in 2026 is citing a "
        "repealed statute.",
        "Bharatiya Sakshya Adhiniyam 2023; Bar & Bench",
        "https://www.barandbench.com/columns/navigating-the-transition-implications-of-the-bhartiya-sakshya-adhiniyam-on-digital-evidence-in-ongoing-trials",
    ),
    Passage(
        "india.dual_signature", "Section 63(4) dual signatory",
        "Section 63(4) BSA moves from a single-signatory certificate to a dual-signatory one: "
        "signed both by the person occupying a responsible official position in relation to "
        "the device, and by an expert where an expert examination was undertaken. A forensic "
        "verdict on submitted evidence is such an examination, so a certificate covering it "
        "requires both signatures.",
        "The Law Codes, certificate under s.65B / s.63(4)",
        "https://thelawcodes.com/article/the-certificate-under-section-65b-of-the-indian-evidence-act-and-section-634-c-of-the-bhartiya-sakshya-adhiniyam-2023/",
    ),
    Passage(
        "india.rbi_stat", "The RBI FY26 figure, correctly read",
        "RBI reported bank fraud of Rs 48,021 crore in FY26, up 46.4 percent year on year, "
        "while the NUMBER of cases fell 57 percent. The rise is driven by the advances/loans "
        "category and by 314 legacy cases worth Rs 30,199 crore reclassified from prior "
        "years. Digital payments fraud actually collapsed, to 293 cases worth Rs 29 crore. "
        "Citing this figure as evidence of a deepfake-driven payments-fraud wave is wrong.",
        "RBI Annual Report FY26 via Business Standard",
        "https://www.business-standard.com/industry/banking/bank-fraud-cases-halve-but-value-climbs-to-48-000-crore-rbi-data-126052900631_1.html",
    ),

    # --- Intent / economics -----------------------------------------------------------
    Passage(
        "intent.classes", "Three kinds of dispute",
        "Disputes divide into three classes needing opposite responses. Criminal fraud: "
        "someone else used the card and the cardholder is a victim; fighting is both wrong "
        "and unwinnable. First-party misuse: the cardholder made the purchase and disputed it "
        "anyway; this is what CE 3.0 exists to defeat. Genuine service failure: the goods "
        "never arrived or were not as described; a fast refund costs less than a contested "
        "chargeback and keeps the customer.",
        "aegis/fusion/intent.py",
    ),
    Passage(
        "economics.break_even", "Break-even is per case, not a flat threshold",
        "The cost of fighting is fixed per case while the payoff scales with the disputed "
        "amount, so a single probability threshold is the wrong shape for the decision. "
        "Fighting is cheaper than conceding exactly when the win probability exceeds "
        "(cost / amount + goods_recovery) divided by (1 - pre-arbitration reversal rate). A "
        "small dispute needs near-certainty; a large one is worth contesting on a coin flip.",
        "aegis/costlab/optimizer.py",
    ),
]


def by_id(pid: str) -> Passage | None:
    return next((p for p in CORPUS if p.id == pid), None)
