"""Visa Acquirer Monitoring Program (VAMP) thresholds — rulebook version 2026.04.

Effective 1 April 2026. Constants sourced in docs/RESEARCH.md section 3.

Two properties of VAMP drive everything in the Cost Lab, and both are routinely missed:

1. The ratio numerator combines TC40 fraud reports AND TC15 chargebacks. A single fraud
   chargeback usually generates both, so it is effectively DOUBLE-COUNTED against the
   merchant. Modelling the ratio as "chargebacks / transactions" understates it by roughly
   a factor of two on the fraud-dispute portion.

2. Below the monthly item floor a merchant is not identified by VAMP at all, regardless of
   how bad the ratio looks. Small merchants worrying about their ratio are often worrying
   about nothing; large merchants a few basis points over are facing real money.

The April 2026 CE 3.0 expansion (RESEARCH.md section 2) added a third lever: qualifying
non-disputed TC40s can now be challenged via Order Insight. A successful challenge removes
numerator without any chargeback ever existing -- the only way to improve the ratio without
either winning disputes or growing transaction volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

RULE_VERSION = "vamp/2026.04"
EFFECTIVE_FROM = date(2026, 4, 1)

# Merchant "Excessive" threshold: 1.5% (150 bps), tightened from 2.2% at launch.
EXCESSIVE_RATIO = 0.015

# Below this many monthly items a merchant is not identified under VAMP regardless of ratio.
MONTHLY_ITEM_FLOOR = 1500

# Flat fee per dispute at the Excessive level, in USD.
FEE_PER_DISPUTE_USD = 8.0

# Applicable regions as of 1 April 2026.
REGIONS = ("US", "CA", "EU", "APAC")

# FX is an assumption, not a rule. It is surfaced in the UI as an editable input so no
# rupee figure in AEGIS is ever presented as more precise than the rate behind it.
DEFAULT_USD_INR = 88.0

# An advisory band below the hard threshold. Not a Visa construct -- acquirers commonly
# enforce stricter internal limits, so AEGIS warns before the network does.
ADVISORY_RATIO = 0.010


@dataclass
class VampState:
    """A merchant's VAMP position for one monthly window."""

    monthly_transactions: int
    tc40_count: int
    tc15_count: int
    usd_inr: float = DEFAULT_USD_INR

    @property
    def numerator(self) -> int:
        """TC40 fraud reports + TC15 chargebacks, combined as VAMP defines it."""
        return self.tc40_count + self.tc15_count

    @property
    def ratio(self) -> float:
        if self.monthly_transactions <= 0:
            return 0.0
        return self.numerator / self.monthly_transactions

    @property
    def identified(self) -> bool:
        """Is this merchant actually in scope for VAMP enforcement?"""
        return self.monthly_transactions >= MONTHLY_ITEM_FLOOR

    @property
    def excessive(self) -> bool:
        return self.identified and self.ratio > EXCESSIVE_RATIO

    @property
    def advisory(self) -> bool:
        return self.identified and not self.excessive and self.ratio > ADVISORY_RATIO

    @property
    def status(self) -> str:
        if not self.identified:
            return "below_floor"
        if self.excessive:
            return "excessive"
        if self.advisory:
            return "advisory"
        return "compliant"

    @property
    def fee_exposure_inr(self) -> float:
        """Rupee fee exposure. Zero unless actually at the Excessive level."""
        if not self.excessive:
            return 0.0
        return self.numerator * FEE_PER_DISPUTE_USD * self.usd_inr

    def headroom_items(self) -> int:
        """How many more TC40+TC15 items before crossing the Excessive threshold.

        Negative means already over -- the magnitude is how many must be removed.
        """
        if not self.identified:
            return MONTHLY_ITEM_FLOOR  # not in scope; report distance to being in scope
        allowed = int(self.monthly_transactions * EXCESSIVE_RATIO)
        return allowed - self.numerator

    def with_deltas(
        self,
        disputes_won: int = 0,
        tc40_challenges_won: int = 0,
        disputes_deflected: int = 0,
    ) -> "VampState":
        """Project the ratio after a policy is applied.

        - `disputes_won`: representment victories. IMPORTANT: winning a representment does
          NOT remove the TC15 from the VAMP ratio -- the chargeback still occurred. It
          recovers the money, not the ratio. Modelling it as ratio relief is a common and
          expensive error, so AEGIS deliberately does not subtract it here.
        - `tc40_challenges_won`: successful Order Insight TC40 challenges under the April
          2026 rule. These DO remove numerator.
        - `disputes_deflected`: pre-disputes resolved before becoming chargebacks (RDR or
          Order Insight). These remove both the TC15 and, typically, the paired TC40.
        """
        return VampState(
            monthly_transactions=self.monthly_transactions,
            tc40_count=max(0, self.tc40_count - tc40_challenges_won - disputes_deflected),
            tc15_count=max(0, self.tc15_count - disputes_deflected),
            usd_inr=self.usd_inr,
        )

    def as_dict(self) -> dict:
        return {
            "rule_version": RULE_VERSION,
            "monthly_transactions": self.monthly_transactions,
            "tc40_count": self.tc40_count,
            "tc15_count": self.tc15_count,
            "numerator": self.numerator,
            "ratio": self.ratio,
            "ratio_bps": self.ratio * 10_000,
            "threshold": EXCESSIVE_RATIO,
            "threshold_bps": EXCESSIVE_RATIO * 10_000,
            "monthly_item_floor": MONTHLY_ITEM_FLOOR,
            "identified": self.identified,
            "status": self.status,
            "fee_per_dispute_usd": FEE_PER_DISPUTE_USD,
            "usd_inr": self.usd_inr,
            "fee_exposure_inr": self.fee_exposure_inr,
            "headroom_items": self.headroom_items(),
        }
