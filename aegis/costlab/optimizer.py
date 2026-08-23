"""Cost Lab: choose the fight/don't-fight threshold in rupees, not in accuracy.

An accuracy-optimal or F1-optimal threshold answers a question no merchant asked. The
question is "how much money do I lose", and the answer depends on economics the model does
not know: what a dispute is worth, what it costs to fight one, how much of the goods value
is recoverable, and how close the merchant is to the VAMP enforcement line.

Two modelling choices here matter more than anything else, and both come straight from the
research in docs/RESEARCH.md:

1. WINNING A REPRESENTMENT DOES NOT REMOVE THE CHARGEBACK FROM THE VAMP RATIO. The TC15
   already happened. Representment recovers the money, not the ratio. Modelling wins as
   ratio relief is a common and expensive error, so `simulate_vamp` refuses to do it.

2. HEADLINE WIN RATES OVERSTATE RECOVERED RUPEES BY ROUGHLY 3x. Published representment win
   rates run ~41%, but net recovery after second chargebacks, pre-arbitration reversals and
   the cost of fighting is only 12-18%. `pre_arb_reversal_rate` is what closes that gap, and
   it defaults to a non-zero value on purpose: a Cost Lab that assumed every win stayed won
   would recommend fighting far more cases than a merchant should.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from rules.vamp import v2026_04 as vamp_rules


@dataclass
class Economics:
    """Merchant-supplied economics. Every value is an input, never a constant."""

    cost_to_fight_inr: float = 1800.0
    """Direct cost of assembling and submitting one representment."""

    staff_cost_per_case_inr: float = 650.0
    """Analyst time per contested case."""

    goods_recovery_rate: float = 0.0
    """Fraction of goods value recovered when a dispute is conceded (returns, resale)."""

    pre_arb_reversal_rate: float = 0.20
    """Fraction of representment WINS later reversed at pre-arbitration.

    This is the single parameter that separates a headline win rate from recovered rupees.
    Set it to zero only if the merchant has data showing their wins actually stick.
    """

    monthly_transactions: int = 42000
    tc40_count: int = 380
    tc15_count: int = 300
    usd_inr: float = vamp_rules.DEFAULT_USD_INR

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyResult:
    name: str
    threshold: float
    n_fought: int
    n_total: int
    total_loss_inr: float
    recovered_inr: float
    fight_cost_inr: float
    conceded_inr: float
    wins: int
    losses: int
    # Rupee-denominated confusion matrix. Counts are secondary; this is what gets decided on.
    tp_inr: float = 0.0
    fp_inr: float = 0.0
    fn_inr: float = 0.0
    tn_inr: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_policy(
    amounts: np.ndarray,
    win_prob: np.ndarray,
    won: np.ndarray,
    econ: Economics,
    threshold: float,
    name: str = "aegis",
    fight_mask: np.ndarray | None = None,
) -> PolicyResult:
    """Total rupee loss for one fight/concede policy on a held-out set.

    `fight_mask` overrides the threshold, so rule-only baselines (fight everything that
    qualifies for CE 3.0) can be scored on exactly the same footing.
    """
    amounts = np.asarray(amounts, dtype=float)
    won = np.asarray(won).astype(bool)
    fight = np.asarray(fight_mask).astype(bool) if fight_mask is not None else (win_prob >= threshold)

    per_case_cost = econ.cost_to_fight_inr + econ.staff_cost_per_case_inr

    # A win only sticks if it survives pre-arbitration. Applying the reversal rate as an
    # expectation rather than a coin flip keeps the curve smooth and the comparison fair.
    stick = 1.0 - econ.pre_arb_reversal_rate

    fought_won = fight & won
    fought_lost = fight & ~won
    conceded = ~fight

    recovered = amounts[fought_won].sum() * stick
    # The reversed share of "wins" is money lost after paying to fight for it.
    reversed_loss = amounts[fought_won].sum() * econ.pre_arb_reversal_rate
    fight_cost = fight.sum() * per_case_cost
    conceded_loss = amounts[conceded].sum() * (1.0 - econ.goods_recovery_rate)
    lost_fights = amounts[fought_lost].sum() * (1.0 - econ.goods_recovery_rate)

    total_loss = fight_cost + conceded_loss + lost_fights + reversed_loss

    return PolicyResult(
        name=name,
        threshold=float(threshold),
        n_fought=int(fight.sum()),
        n_total=int(len(amounts)),
        total_loss_inr=float(total_loss),
        recovered_inr=float(recovered),
        fight_cost_inr=float(fight_cost),
        conceded_inr=float(conceded_loss),
        wins=int(fought_won.sum()),
        losses=int(fought_lost.sum()),
        # Rupee confusion matrix, from the merchant's point of view:
        #   TP  fought and won      -> rupees recovered (net of reversal)
        #   FP  fought and lost     -> rupees burned on a losing fight
        #   FN  conceded but winnable -> rupees left on the table
        #   TN  conceded and unwinnable -> correctly not spent
        tp_inr=float(recovered),
        fp_inr=float(fought_lost.sum() * per_case_cost),
        fn_inr=float(amounts[conceded & won].sum() * stick),
        tn_inr=float((conceded & ~won).sum() * per_case_cost),
        tp=int(fought_won.sum()),
        fp=int(fought_lost.sum()),
        fn=int((conceded & won).sum()),
        tn=int((conceded & ~won).sum()),
    )


def sweep(
    amounts: np.ndarray,
    win_prob: np.ndarray,
    won: np.ndarray,
    econ: Economics,
    n_points: int = 101,
) -> dict:
    """Sweep the decision threshold and return the full rupee-loss curve plus baselines."""
    thresholds = np.linspace(0.0, 1.0, n_points)
    curve = [
        evaluate_policy(amounts, win_prob, won, econ, float(t), name=f"t={t:.2f}")
        for t in thresholds
    ]
    losses = np.array([c.total_loss_inr for c in curve])
    best_i = int(losses.argmin())
    best = curve[best_i]
    best.name = "aegis_optimal"

    fight_all = evaluate_policy(amounts, win_prob, won, econ, 0.0, name="fight_all")
    fight_none = evaluate_policy(amounts, win_prob, won, econ, 1.01, name="fight_none")

    return {
        "curve": [
            {"threshold": float(t), "total_loss_inr": float(l), "n_fought": int(c.n_fought)}
            for t, l, c in zip(thresholds, losses, curve)
        ],
        "optimal": best.as_dict(),
        "baselines": {"fight_all": fight_all.as_dict(), "fight_none": fight_none.as_dict()},
        "savings_vs_fight_all_inr": float(fight_all.total_loss_inr - best.total_loss_inr),
        "savings_vs_fight_none_inr": float(fight_none.total_loss_inr - best.total_loss_inr),
        "economics": econ.as_dict(),
    }


def compare_with_rule_baseline(
    amounts: np.ndarray,
    win_prob: np.ndarray,
    won: np.ndarray,
    qualified: np.ndarray,
    econ: Economics,
) -> dict:
    """Add the 'fight everything CE 3.0 qualifies' baseline to the sweep.

    This is the strongest naive policy and the fairest comparison: it is what a merchant
    would do with the rulebook alone and no model at all. If AEGIS cannot beat it in rupees,
    the model is not earning its place.
    """
    out = sweep(amounts, win_prob, won, econ)
    rule = evaluate_policy(
        amounts, win_prob, won, econ, 0.0, name="fight_if_qualified",
        fight_mask=np.asarray(qualified).astype(bool),
    )
    out["baselines"]["fight_if_qualified"] = rule.as_dict()
    out["savings_vs_rule_only_inr"] = float(rule.total_loss_inr - out["optimal"]["total_loss_inr"])
    return out


def simulate_vamp(
    econ: Economics,
    tc40_challenges_won: int = 0,
    disputes_deflected: int = 0,
) -> dict:
    """Project the VAMP ratio under a policy.

    Note what is NOT an input here: representment wins. A won chargeback is still a
    chargeback in the ratio. Only pre-dispute deflection and successful TC40 challenges --
    the latter available since the 18 April 2026 CE 3.0 expansion -- remove numerator.
    """
    before = vamp_rules.VampState(
        monthly_transactions=econ.monthly_transactions,
        tc40_count=econ.tc40_count,
        tc15_count=econ.tc15_count,
        usd_inr=econ.usd_inr,
    )
    after = before.with_deltas(
        tc40_challenges_won=tc40_challenges_won,
        disputes_deflected=disputes_deflected,
    )
    return {
        "before": before.as_dict(),
        "after": after.as_dict(),
        "fee_saving_inr": float(before.fee_exposure_inr - after.fee_exposure_inr),
        "crosses_back_under_threshold": bool(before.excessive and not after.excessive),
        "note": (
            "Representment wins are deliberately excluded from this projection. Winning a "
            "dispute recovers the money but leaves the TC15 in the VAMP numerator. Only "
            "pre-dispute deflection and successful TC40 challenges reduce the ratio."
        ),
    }


def ev_threshold(amounts: np.ndarray, econ: Economics, kappa: float = 1.0) -> np.ndarray:
    """The break-even win probability for each case, given its value.

    A single flat probability threshold is the wrong shape for this decision, because the
    cost of fighting is fixed per case while the payoff scales with the disputed amount.
    Fighting a Rs 400 dispute needs near-certainty to be worth Rs 2,450 of effort; a
    Rs 40,000 dispute is worth contesting on a coin flip.

    Derivation. Conceding costs `amount * (1 - g)`. Fighting costs
    `c + amount * (1 - p * stick)`, where `stick = 1 - pre_arb_reversal_rate`. Fighting is
    cheaper exactly when:

        c + amount * (1 - p*stick)  <  amount * (1 - g)
        =>  p  >  (c/amount + g) / stick

    `kappa` scales that break-even point to express risk appetite: kappa > 1 is more
    conservative (fight less), kappa < 1 more aggressive. Sweeping kappa gives the same
    kind of tunable curve as a flat threshold, but around the economically correct shape.
    """
    amounts = np.asarray(amounts, dtype=float)
    c = econ.cost_to_fight_inr + econ.staff_cost_per_case_inr
    stick = max(1.0 - econ.pre_arb_reversal_rate, 1e-6)
    with np.errstate(divide="ignore", invalid="ignore"):
        thr = (c / np.maximum(amounts, 1.0) + econ.goods_recovery_rate) / stick
    return np.clip(thr * kappa, 0.0, 1.0)


def evaluate_ev_policy(
    amounts: np.ndarray,
    win_prob: np.ndarray,
    won: np.ndarray,
    econ: Economics,
    kappa: float = 1.0,
    name: str = "aegis_expected_value",
) -> PolicyResult:
    """Fight when the case's own break-even probability is met."""
    fight = np.asarray(win_prob, dtype=float) >= ev_threshold(amounts, econ, kappa)
    r = evaluate_policy(amounts, win_prob, won, econ, 0.0, name=name, fight_mask=fight)
    r.threshold = float(kappa)
    return r


def sweep_ev(
    amounts: np.ndarray,
    win_prob: np.ndarray,
    won: np.ndarray,
    econ: Economics,
    n_points: int = 81,
) -> dict:
    """Sweep risk appetite around the per-case break-even point."""
    kappas = np.linspace(0.2, 3.0, n_points)
    curve = [evaluate_ev_policy(amounts, win_prob, won, econ, float(k)) for k in kappas]
    losses = np.array([c.total_loss_inr for c in curve])
    best = curve[int(losses.argmin())]
    best.name = "aegis_ev_optimal"
    return {
        "curve": [
            {"kappa": float(k), "total_loss_inr": float(l), "n_fought": int(c.n_fought)}
            for k, l, c in zip(kappas, losses, curve)
        ],
        "optimal": best.as_dict(),
    }
