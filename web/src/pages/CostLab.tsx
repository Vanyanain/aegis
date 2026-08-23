import { useEffect, useMemo, useState } from "react";
import { inr, inrCompact, pct, post } from "../api";

type Policy = {
  name: string; threshold: number; n_fought: number; n_total: number;
  total_loss_inr: number; recovered_inr: number; wins: number; losses: number;
  tp_inr: number; fp_inr: number; fn_inr: number; tn_inr: number;
  tp: number; fp: number; fn: number; tn: number;
};
type Resp = {
  optimal: Policy;
  baselines: Record<string, Policy>;
  expected_value: { curve: { kappa: number; total_loss_inr: number; n_fought: number }[]; optimal: Policy };
  vamp: { before: any; after: any; fee_saving_inr: number; crosses_back_under_threshold: boolean; note: string };
  test_set: { n: number; exposure_inr: number; actual_win_rate: number; note: string };
};

const DEFAULTS = {
  cost_to_fight_inr: 1800, staff_cost_per_case_inr: 650, goods_recovery_rate: 0,
  pre_arb_reversal_rate: 0.2, monthly_transactions: 42000, tc40_count: 380,
  tc15_count: 300, usd_inr: 88,
};

export default function CostLab() {
  const [econ, setEcon] = useState(DEFAULTS);
  const [d, setD] = useState<Resp | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    const t = setTimeout(() => {
      post<Resp>("/api/costlab", econ).then(setD).catch((e) => setErr(String(e)));
    }, 220);
    return () => clearTimeout(t);
  }, [econ]);

  const set = (k: keyof typeof DEFAULTS) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setEcon((s) => ({ ...s, [k]: Number(e.target.value) }));

  const policies = useMemo(() => {
    if (!d) return [];
    return [
      { ...d.baselines.fight_all, display: "Fight everything" },
      { ...d.baselines.fight_none, display: "Fight nothing" },
      { ...d.baselines.fight_if_qualified, display: "Fight if CE 3.0 qualifies" },
      { ...d.optimal, display: "AEGIS · flat threshold" },
      { ...d.expected_value.optimal, display: "AEGIS · expected value" },
    ];
  }, [d]);

  const best = policies.length ? Math.min(...policies.map((p) => p.total_loss_inr)) : 0;

  return (
    <>
      <div className="page-head">
        <h2>Cost Lab</h2>
        <p>
          The decision threshold is chosen in rupees, not in accuracy. Enter your real economics;
          every policy below is scored on the same held-out disputes, from customers unseen during
          training.
        </p>
      </div>

      {err && <div className="loading red">{err}</div>}

      <div className="grid" style={{ gridTemplateColumns: "270px 1fr", gap: 12 }}>
        <div className="card">
          <h3>Your economics</h3>
          {([
            ["cost_to_fight_inr", "Cost to fight one case (₹)", "Assembly and submission cost per representment."],
            ["staff_cost_per_case_inr", "Analyst time per case (₹)", ""],
            ["goods_recovery_rate", "Goods recovery rate (0–1)", "Fraction of value recovered when you concede."],
            ["pre_arb_reversal_rate", "Pre-arb reversal rate (0–1)", "Share of WINS later reversed. This is what separates a headline win rate from recovered rupees."],
            ["monthly_transactions", "Monthly transactions", ""],
            ["tc40_count", "TC40 fraud reports / month", ""],
            ["tc15_count", "TC15 chargebacks / month", ""],
            ["usd_inr", "USD → INR", "VAMP fees are set in dollars; this rate is an assumption, not a rule."],
          ] as const).map(([k, label, hint]) => (
            <div className="field" key={k}>
              <label>{label}</label>
              <input type="number" step="any" value={econ[k]} onChange={set(k)} />
              {hint && <div className="hint">{hint}</div>}
            </div>
          ))}
          <button className="btn" onClick={() => setEcon(DEFAULTS)}>Reset</button>
        </div>

        <div>
          {!d ? (
            <div className="loading">sweeping thresholds…</div>
          ) : (
            <>
              <div className="card" style={{ marginBottom: 12 }}>
                <h3>Total rupee loss by policy · {d.test_set.n} held-out disputes · {inrCompact(d.test_set.exposure_inr)} exposed</h3>
                <div className="scroll-x">
                  <table>
                    <thead>
                      <tr>
                        <th>Policy</th>
                        <th className="r">Cases fought</th>
                        <th className="r">Wins</th>
                        <th className="r">Total ₹ loss</th>
                        <th className="r">vs best</th>
                      </tr>
                    </thead>
                    <tbody>
                      {policies.map((p) => {
                        const isBest = p.total_loss_inr === best;
                        return (
                          <tr key={p.display}>
                            <td style={{ fontWeight: isBest ? 600 : 400 }}>
                              {p.display}
                              {isBest && <span className="pill ok" style={{ marginLeft: 8 }}>optimal</span>}
                            </td>
                            <td className="r num">{p.n_fought}</td>
                            <td className="r num dim">{p.wins}</td>
                            <td className={`r num ${isBest ? "teal" : ""}`} style={{ fontWeight: isBest ? 700 : 400 }}>
                              {inr(p.total_loss_inr)}
                            </td>
                            <td className="r num dim">
                              {isBest ? "—" : "+" + inrCompact(p.total_loss_inr - best)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="callout teal" style={{ marginTop: 12 }}>
                  The expected-value policy fights <strong>{d.expected_value.optimal.n_fought} cases</strong> where
                  “fight if qualified” fights <strong>{d.baselines.fight_if_qualified.n_fought}</strong> — and still
                  loses less money. Cost to fight is fixed per case while the payoff scales with
                  the amount, so a flat probability threshold is simply the wrong shape for this
                  decision.
                </div>
              </div>

              <div className="grid g2" style={{ marginBottom: 12 }}>
                <div className="card">
                  <h3>Loss curve · risk appetite</h3>
                  <LossCurve curve={d.expected_value.curve} optimal={d.expected_value.optimal.threshold} />
                  <div className="footnote">
                    κ scales each case's own break-even probability. κ &gt; 1 fights less, κ &lt; 1
                    fights more. The marked point is the rupee minimum.
                  </div>
                </div>

                <div className="card">
                  <h3>Confusion matrix, in rupees</h3>
                  <div className="grid g2" style={{ gap: 8 }}>
                    <Cell label="Fought & won" sub={`${d.expected_value.optimal.tp} cases`} value={d.expected_value.optimal.tp_inr} color="var(--teal)" note="recovered, net of reversal" />
                    <Cell label="Fought & lost" sub={`${d.expected_value.optimal.fp} cases`} value={d.expected_value.optimal.fp_inr} color="var(--red)" note="burned on losing fights" />
                    <Cell label="Conceded, was winnable" sub={`${d.expected_value.optimal.fn} cases`} value={d.expected_value.optimal.fn_inr} color="var(--amber)" note="left on the table" />
                    <Cell label="Conceded, unwinnable" sub={`${d.expected_value.optimal.tn} cases`} value={d.expected_value.optimal.tn_inr} color="var(--text-3)" note="correctly not spent" />
                  </div>
                  <div className="callout" style={{ marginTop: 12 }}>
                    <strong>False-positive cost is {inr(d.expected_value.optimal.fp_inr)}</strong> — real money
                    spent fighting cases that lost. Counting cases instead of rupees would hide it.
                  </div>
                </div>
              </div>

              <div className="card">
                <h3>VAMP simulation · what actually moves the ratio</h3>
                <div className="grid g4">
                  <div className="stat">
                    <div className="label">Before</div>
                    <div className={`value ${d.vamp.before.excessive ? "red" : "teal"}`}>{d.vamp.before.ratio_bps.toFixed(0)}<span style={{ fontSize: 12 }}> bps</span></div>
                    <div className="sub">{d.vamp.before.status}</div>
                  </div>
                  <div className="stat">
                    <div className="label">After TC40 challenges</div>
                    <div className={`value ${d.vamp.after.excessive ? "red" : "teal"}`}>{d.vamp.after.ratio_bps.toFixed(0)}<span style={{ fontSize: 12 }}> bps</span></div>
                    <div className="sub">{d.vamp.after.status}</div>
                  </div>
                  <div className="stat">
                    <div className="label">Fee saving</div>
                    <div className="value teal">{inrCompact(d.vamp.fee_saving_inr)}</div>
                    <div className="sub">at $8/dispute</div>
                  </div>
                  <div className="stat">
                    <div className="label">Back under threshold</div>
                    <div className={`value ${d.vamp.crosses_back_under_threshold ? "teal" : "amber"}`}>
                      {d.vamp.crosses_back_under_threshold ? "YES" : "NO"}
                    </div>
                    <div className="sub">1.5% enforcement line</div>
                  </div>
                </div>
                <div className="callout" style={{ marginTop: 12 }}>{d.vamp.note}</div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function Cell({ label, sub, value, color, note }: { label: string; sub: string; value: number; color: string; note: string }) {
  return (
    <div style={{ background: "var(--bg-2)", border: "1px solid var(--line)", borderRadius: 4, padding: "10px 12px" }}>
      <div className="dimmer" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.07em" }}>{label}</div>
      <div className="num" style={{ fontSize: 17, fontWeight: 700, color, marginTop: 4 }}>{inrCompact(value)}</div>
      <div className="dimmer" style={{ fontSize: 10.5, marginTop: 2 }}>{sub} · {note}</div>
    </div>
  );
}

function LossCurve({ curve, optimal }: { curve: { kappa: number; total_loss_inr: number }[]; optimal: number }) {
  const W = 460, H = 190, P = 34;
  if (!curve.length) return null;
  const xs = curve.map((c) => c.kappa);
  const ys = curve.map((c) => c.total_loss_inr);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const px = (v: number) => P + ((v - x0) / (x1 - x0)) * (W - P - 10);
  const py = (v: number) => H - P - ((v - y0) / (y1 - y0 || 1)) * (H - P - 12);
  const path = curve.map((c, i) => `${i ? "L" : "M"}${px(c.kappa).toFixed(1)},${py(c.total_loss_inr).toFixed(1)}`).join(" ");
  const opt = curve.reduce((a, b) => (b.total_loss_inr < a.total_loss_inr ? b : a));

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Total rupee loss against risk appetite">
      <line x1={P} y1={H - P} x2={W - 10} y2={H - P} stroke="var(--line-2)" />
      <line x1={P} y1={12} x2={P} y2={H - P} stroke="var(--line-2)" />
      <path d={path} fill="none" stroke="var(--teal)" strokeWidth="1.6" />
      <line x1={px(opt.kappa)} y1={12} x2={px(opt.kappa)} y2={H - P} stroke="var(--amber)" strokeWidth="1" strokeDasharray="3 3" />
      <circle cx={px(opt.kappa)} cy={py(opt.total_loss_inr)} r="3.5" fill="var(--amber)" />
      <text x={px(opt.kappa) + 6} y={py(opt.total_loss_inr) - 7} fill="var(--amber)">
        κ={opt.kappa.toFixed(2)} · {inrCompact(opt.total_loss_inr)}
      </text>
      <text x={P} y={H - 12}>{x0.toFixed(1)}</text>
      <text x={W - 26} y={H - 12}>{x1.toFixed(1)}</text>
      <text x={4} y={16}>{inrCompact(y1)}</text>
      <text x={4} y={H - P}>{inrCompact(y0)}</text>
    </svg>
  );
}
