import { useEffect, useState } from "react";
import { get, inrCompact, pct } from "../api";

type El = {
  field: string; element: string; tier: string;
  coverage_all_time: number; coverage_last_90d: number;
  would_unlock_cases: number; would_unlock_inr: number;
};

type Ov = {
  portfolio: { transactions: number; customers: number; disputes: number; disputes_104: number; exposure_inr: number; exposure_104_inr: number };
  ce3: { qualified_rate: number; qualified_count: number; defensible_inr: number; undefendable_inr: number; naive_qualified_rate: number; naive_false_positives: number; naive_false_positive_inr: number };
  capture_readiness: { elements: El[]; blocking_gaps: Record<string, number>; lag_days: number; note: string };
  vamp: { ratio_bps: number; threshold_bps: number; status: string; numerator: number; monthly_transactions: number; fee_exposure_inr: number; headroom_items: number; identified: boolean };
};

const GAP_LABEL: Record<string, string> = {
  INSUFFICIENT_ELIGIBLE_PRIORS: "Not enough eligible prior transactions",
  NO_MAIN_ANCHOR: "Only Secondary elements matched — no Main anchor",
  ELEMENTS_INSUFFICIENT: "Matched elements don't satisfy the tier rule",
  MISSING_PRODUCT_DESCRIPTION: "Product description missing",
  NO_ELEMENTS_CAPTURED: "No CE 3.0 elements captured at all",
  MAIN_WITHOUT_PARTNER: "One Main element, nothing pairs with it",
  NO_ELEMENTS_MATCH: "Elements captured but values differ",
  NO_PRIOR_HISTORY: "First-time customer — no history",
};

export default function Overview() {
  const [d, setD] = useState<Ov | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get<Ov>("/api/overview").then(setD).catch((e) => setErr(String(e))); }, []);

  if (err) return <div className="loading red">{err}</div>;
  if (!d) return <div className="loading">loading portfolio…</div>;

  const { portfolio: p, ce3, vamp } = d;
  const vampOver = vamp.ratio_bps > vamp.threshold_bps;
  // Element rows are deduplicated: fingerprint and device ID share one CE 3.0 slot, so
  // showing both as separate unlocks would double-count the same opportunity.
  const els = d.capture_readiness.elements
    .filter((e, i, a) => a.findIndex((x) => x.element === e.element) === i)
    .sort((a, b) => b.would_unlock_inr - a.would_unlock_inr);

  return (
    <>
      <div className="page-head">
        <h2>Portfolio</h2>
        <p>
          What this merchant can actually defend today, what it would cost to be wrong, and which
          single data field would change the answer.
        </p>
      </div>

      <div className="grid g4" style={{ marginBottom: 12 }}>
        <div className="card stat">
          <div className="label">Disputes on 10.4</div>
          <div className="value">{p.disputes_104.toLocaleString("en-IN")}</div>
          <div className="sub">of {p.disputes.toLocaleString("en-IN")} total · {inrCompact(p.exposure_104_inr)} exposed</div>
        </div>
        <div className="card stat">
          <div className="label">CE 3.0 qualified</div>
          <div className="value teal">{pct(ce3.qualified_rate)}</div>
          <div className="sub">{ce3.qualified_count.toLocaleString("en-IN")} cases · {inrCompact(ce3.defensible_inr)} defensible</div>
        </div>
        <div className="card stat">
          <div className="label">Undefendable</div>
          <div className="value amber">{inrCompact(ce3.undefendable_inr)}</div>
          <div className="sub">10.4 disputes that fail the gate</div>
        </div>
        <div className="card stat">
          <div className="label">VAMP ratio</div>
          <div className={`value ${vampOver ? "red" : "teal"}`}>{vamp.ratio_bps.toFixed(0)}<span style={{ fontSize: 13 }}> bps</span></div>
          <div className="sub">
            threshold {vamp.threshold_bps.toFixed(0)} bps ·{" "}
            <span className={vampOver ? "red" : "teal"}>{vamp.status}</span>
          </div>
        </div>
      </div>

      {/* The single most load-bearing finding in the product. */}
      <div className="card" style={{ marginBottom: 12, borderLeft: "2px solid var(--red)" }}>
        <h3>The rule most implementations get wrong</h3>
        <div style={{ display: "flex", gap: 28, flexWrap: "wrap", alignItems: "flex-start" }}>
          <div>
            <div className="dimmer" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.08em" }}>Naive “any 2 of 4”</div>
            <div className="num red" style={{ fontSize: 25, fontWeight: 700 }}>{pct(ce3.naive_qualified_rate)}</div>
            <div className="dim" style={{ fontSize: 11.5 }}>would be called winnable</div>
          </div>
          <div style={{ fontSize: 22, color: "var(--text-3)", alignSelf: "center" }}>→</div>
          <div>
            <div className="dimmer" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.08em" }}>Actual Visa tier rule</div>
            <div className="num teal" style={{ fontSize: 25, fontWeight: 700 }}>{pct(ce3.qualified_rate)}</div>
            <div className="dim" style={{ fontSize: 11.5 }}>genuinely qualify</div>
          </div>
          <div style={{ flex: 1, minWidth: 300 }}>
            <div className="callout red" style={{ borderLeftColor: "var(--red)" }}>
              <strong>{ce3.naive_false_positives.toLocaleString("en-IN")} cases worth {inrCompact(ce3.naive_false_positive_inr)}</strong> match two
              data elements but have <strong>no Main anchor</strong> — an IP or device match. Visa
              rejects these. A tool reading the rule as “any two of four” tells the merchant to
              fight cases they will certainly lose, and bills them for the privilege.
            </div>
          </div>
        </div>
      </div>

      <div className="grid g2" style={{ marginBottom: 12 }}>
        <div className="card">
          <h3>Capture readiness — the pre-dispute lever</h3>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>CE 3.0 element</th>
                  <th className="r">Coverage 90d</th>
                  <th className="r">Would unlock</th>
                </tr>
              </thead>
              <tbody>
                {els.map((e) => (
                  <tr key={e.element}>
                    <td>
                      <span className="mono" style={{ fontSize: 11.5 }}>{e.element}</span>
                      <span className={`tier ${e.tier}`} style={{ marginLeft: 7, fontSize: 8.5, padding: "0 4px", border: "1px solid var(--line-2)", borderRadius: 2, color: e.tier === "main" ? "var(--blue)" : "var(--text-3)", textTransform: "uppercase" }}>{e.tier}</span>
                    </td>
                    <td className="r">
                      <span className="num" style={{ color: e.coverage_last_90d < 0.5 ? "var(--amber)" : "var(--text)" }}>
                        {pct(e.coverage_last_90d, 0)}
                      </span>
                      <div className="bar" style={{ marginTop: 4 }}>
                        <span style={{ width: pct(e.coverage_last_90d, 0), background: e.coverage_last_90d < 0.5 ? "var(--amber)" : "var(--teal)" }} />
                      </div>
                    </td>
                    <td className="r num">
                      {e.would_unlock_cases > 0 ? (
                        <>
                          <span className="teal">{inrCompact(e.would_unlock_inr)}</span>
                          <div className="dimmer" style={{ fontSize: 10.5 }}>{e.would_unlock_cases} cases</div>
                        </>
                      ) : (
                        <span className="dimmer">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="callout teal" style={{ marginTop: 12 }}>
            CE 3.0 needs priors aged <strong>120–364 days</strong>. Whatever you start capturing
            today first changes qualification about <strong>{d.capture_readiness.lag_days} days
            from now</strong> — by the time a dispute lands, the data either exists or it doesn't.
            This is the only lever that works before the fight starts.
          </div>
        </div>

        <div className="card">
          <h3>Why 10.4 disputes fail the gate</h3>
          <div className="scroll-x">
            <table>
              <thead>
                <tr><th>Blocking gap</th><th className="r">Cases</th><th className="r">Share</th></tr>
              </thead>
              <tbody>
                {Object.entries(d.capture_readiness.blocking_gaps)
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 7)
                  .map(([code, n]) => (
                    <tr key={code}>
                      <td>
                        <div style={{ fontSize: 12 }}>{GAP_LABEL[code] ?? code}</div>
                        <div className="mono dimmer" style={{ fontSize: 9.5 }}>{code}</div>
                      </td>
                      <td className="r num">{n.toLocaleString("en-IN")}</td>
                      <td className="r num dim">{pct(n / Math.max(p.disputes_104, 1), 0)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <div className="footnote">
            <code>NO_MAIN_ANCHOR</code> is the tier rule biting: elements matched, but none of them
            an IP or device. <code>INSUFFICIENT_ELIGIBLE_PRIORS</code> is usually timing, not data —
            those customers become defensible on their own.
          </div>
        </div>
      </div>

      <div className="card">
        <h3>VAMP position · rulebook 2026.04</h3>
        <div className="grid g4">
          <div><div className="dimmer" style={{ fontSize: 10.5 }}>NUMERATOR (TC40 + TC15)</div><div className="num" style={{ fontSize: 17, fontWeight: 700 }}>{vamp.numerator.toLocaleString("en-IN")}</div></div>
          <div><div className="dimmer" style={{ fontSize: 10.5 }}>MONTHLY ITEMS</div><div className="num" style={{ fontSize: 17, fontWeight: 700 }}>{vamp.monthly_transactions.toLocaleString("en-IN")}</div></div>
          <div><div className="dimmer" style={{ fontSize: 10.5 }}>HEADROOM</div><div className={`num ${vamp.headroom_items < 0 ? "red" : "teal"}`} style={{ fontSize: 17, fontWeight: 700 }}>{vamp.headroom_items > 0 ? "+" : ""}{vamp.headroom_items.toLocaleString("en-IN")}</div></div>
          <div><div className="dimmer" style={{ fontSize: 10.5 }}>FEE EXPOSURE</div><div className={`num ${vamp.fee_exposure_inr > 0 ? "red" : "teal"}`} style={{ fontSize: 17, fontWeight: 700 }}>{inrCompact(vamp.fee_exposure_inr)}</div></div>
        </div>
        <div className="callout" style={{ marginTop: 12 }}>
          A fraud chargeback files <strong>both</strong> a TC40 and a TC15, so it counts twice in
          this ratio. Winning a representment recovers the money but leaves the TC15 in the
          numerator — only pre-dispute deflection and TC40 challenges move the ratio itself.
        </div>
      </div>
    </>
  );
}
