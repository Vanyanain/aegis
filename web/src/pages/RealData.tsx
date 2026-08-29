import { useEffect, useState } from "react";
import { count, get, inrCompact, pct } from "../api";

type Qual = {
  n_transactions: number; n_chargebacks: number; n_assessable: number;
  qualified: number; qualified_rate: number; naive_qualified_rate: number;
  naive_false_positives: number; naive_false_positive_inr: number;
  defensible_inr: number; undefendable_inr: number;
  funnel: { assessable: number; cleared_prior_gate: number; qualified: number; blocked_no_main_anchor: number };
  blocking_gaps: Record<string, number>;
  capture_coverage: Record<string, number>;
  constraints: Record<string, string | number>;
  sensitivity?: Record<string, { qualified: number; rate: number }>;
};
type Rings = {
  n_rings: number; n_entities_in_rings: number; n_transactions_in_rings: number;
  chargeback_rate_in_rings: number; chargeback_rate_outside: number; lift: number;
  chargeback_value_in_rings_inr: number;
  largest_rings: { ring_id: number; n_entities: number; n_devices: number; n_transactions: number; n_chargebacks: number; chargeback_rate: number; chargeback_value_inr: number }[];
  method: string;
};
type M = {
  qualification?: Qual;
  rings?: Rings;
  side_a?: any; side_b?: any; vision?: any; adversarial?: any;
  datasets: Record<string, { name: string; n: number; note: string; label?: string }>;
};

// What each countermeasure actually destroys, so the recall drop is readable rather than
// just a number going down.
const ATTACK_EFFECT: Record<string, string> = {
  none: "baseline",
  strip_exif: "provenance group",
  recompress: "compression-history discontinuity",
  resize: "JPEG block grid",
  add_noise: "local noise floor",
  screenshot: "all of the above",
};

const GAP_LABEL: Record<string, string> = {
  INSUFFICIENT_ELIGIBLE_PRIORS: "Not enough eligible prior transactions",
  NO_PRIOR_HISTORY: "No prior history on this credential at all",
  ELEMENTS_INSUFFICIENT: "Matched elements don't satisfy the tier rule",
  NO_MAIN_ANCHOR: "Only Secondary elements — no IP or device anchor",
  NO_ELEMENTS_CAPTURED: "No CE 3.0 elements captured",
  MAIN_WITHOUT_PARTNER: "One Main element, nothing pairs with it",
};

export default function RealData() {
  const [m, setM] = useState<M | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get<M>("/api/real/metrics").then(setM).catch((e) => setErr(String(e))); }, []);

  if (err) return <div className="loading red">{err}</div>;
  if (!m) return <div className="loading">loading real-data results…</div>;

  const q = m.qualification;
  const r = m.rings;
  const a = m.side_a;
  const b = m.side_b;

  return (
    <>
      <div className="page-head">
        <h2>Real data</h2>
        <p>
          Everything on this page is measured on public datasets, not simulated. The transaction
          side is IEEE-CIS (Vesta), whose fraud label the provider defines as a reported
          chargeback. The evidence side is real receipt photographs from CORD and SROIE.
        </p>
      </div>

      <div className="grid g2" style={{ marginBottom: 12 }}>
        {Object.entries(m.datasets).map(([k, d]) => (
          <div className="card" key={k}>
            <h3>{k === "transactions" ? "Transaction data" : "Evidence data"}</h3>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{d.name}</div>
            <div className="num teal" style={{ fontSize: 21, fontWeight: 700 }}>{count(d.n)}</div>
            <div className="dim" style={{ fontSize: 11.5, marginTop: 6, lineHeight: 1.55 }}>{d.note}</div>
            {d.label && <div className="footnote">Label: <code>{d.label}</code></div>}
            {k === "transactions" && (
              <div className="footnote">
                Amounts are USD in the source and shown in ₹ at a stated rate of 88, since the
                rulebook and VAMP economics this feeds are India-denominated.
              </div>
            )}
          </div>
        ))}
      </div>

      {q && (
        <div className="card" style={{ marginBottom: 12, borderLeft: "2px solid var(--red)" }}>
          <h3>Visa CE 3.0, applied unmodified to {count(q.n_chargebacks)} real chargebacks</h3>

          {/* The funnel is the finding. A single rate would hide where cases actually die. */}
          <div className="scroll-x">
            <table>
              <thead><tr><th>Stage</th><th className="r">Cases</th><th className="r">Share</th><th>What it means</th></tr></thead>
              <tbody>
                <tr>
                  <td>Real reported chargebacks</td>
                  <td className="r num">{count(q.n_chargebacks)}</td>
                  <td className="r num dim">100%</td>
                  <td className="dim" style={{ fontSize: 11.5 }}>every chargeback in the dataset</td>
                </tr>
                <tr>
                  <td>Assessable</td>
                  <td className="r num">{count(q.n_assessable)}</td>
                  <td className="r num dim">{pct(q.n_assessable / q.n_chargebacks, 0)}</td>
                  <td className="dim" style={{ fontSize: 11.5 }}>raised late enough that a 120-day lookback exists</td>
                </tr>
                <tr>
                  <td>Clear the prior-history gate</td>
                  <td className="r num amber">{count(q.funnel.cleared_prior_gate)}</td>
                  <td className="r num dim">{pct(q.funnel.cleared_prior_gate / q.n_assessable)}</td>
                  <td className="dim" style={{ fontSize: 11.5 }}>have ≥2 eligible priors on the same credential</td>
                </tr>
                <tr style={{ background: "var(--bg-2)" }}>
                  <td style={{ fontWeight: 600 }}>Qualify for CE 3.0</td>
                  <td className="r num teal" style={{ fontWeight: 700 }}>{q.qualified}</td>
                  <td className="r num dim">{pct(q.qualified_rate, 2)}</td>
                  <td className="dim" style={{ fontSize: 11.5 }}>elements match under the real tier rule</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="grid g2" style={{ marginTop: 14 }}>
            <div className="callout red">
              <strong>
                {q.funnel.blocked_no_main_anchor} of the {q.funnel.cleared_prior_gate} cases that
                clear the history gate ({pct(q.funnel.blocked_no_main_anchor / Math.max(q.funnel.cleared_prior_gate, 1), 0)})
                fail for one reason: no Main anchor was ever captured.
              </strong>{" "}
              They match on address or email but on no IP and no device. Visa rejects them. A
              naive “any two of four” reading passes all of them —{" "}
              <strong>{q.naive_false_positives} false positives worth {inrCompact(q.naive_false_positive_inr)}</strong>{" "}
              of representments that would be filed and lost.
            </div>
            <div className="callout">
              <strong>What the funnel actually says.</strong> CE 3.0 was designed for friendly
              fraud — a real repeat customer disputing a real purchase. This dataset is dominated
              by criminal card-not-present fraud, where the cardholder has no relationship with
              the merchant at all: {pct((q.blocking_gaps.NO_PRIOR_HISTORY ?? 0) / q.n_assessable, 0)}{" "}
              have no prior history whatsoever. The remedy is not a better model, it is capture.
            </div>
          </div>

          <div className="grid g2" style={{ marginTop: 14 }}>
            <div>
              <h3>Capture coverage, measured</h3>
              <table>
                <thead><tr><th>CE 3.0 element</th><th className="r">Coverage</th></tr></thead>
                <tbody>
                  {Object.entries(q.capture_coverage).map(([k, v]) => (
                    <tr key={k}>
                      <td className="mono" style={{ fontSize: 11.5 }}>{k}</td>
                      <td className="r num">
                        {v > 0 ? (
                          <>
                            <span className={v < 0.5 ? "amber" : "teal"}>{pct(v, 0)}</span>
                            <div className="bar" style={{ marginTop: 4 }}>
                              <span style={{ width: pct(v, 0), background: v < 0.5 ? "var(--amber)" : "var(--teal)" }} />
                            </div>
                          </>
                        ) : <span className="dimmer">anonymised</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="footnote">
                Device fingerprint is the <strong>only Main element available</strong> — the
                dataset anonymises network identifiers, so the “two Main” path can't be assessed
                and every qualifying case must go through one Main + one Secondary. Both
                constraints push the measured rate <em>below</em> reality.
              </div>
            </div>
            <div>
              <h3>Where cases die</h3>
              <table>
                <thead><tr><th>Blocking gap</th><th className="r">Cases</th></tr></thead>
                <tbody>
                  {Object.entries(q.blocking_gaps).slice(0, 6).map(([k, v]) => (
                    <tr key={k}>
                      <td>
                        <div style={{ fontSize: 12 }}>{GAP_LABEL[k] ?? k}</div>
                        <div className="mono dimmer" style={{ fontSize: 9.5 }}>{k}</div>
                      </td>
                      <td className="r num">{count(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {q.sensitivity && (
                <>
                  <h3 style={{ marginTop: 14 }}>Sensitivity to the prior-age floor</h3>
                  <table>
                    <tbody>
                      {Object.entries(q.sensitivity).map(([d, s]) => (
                        <tr key={d}>
                          <td className="mono" style={{ fontSize: 11.5 }}>priors ≥ {d}d</td>
                          <td className="r num">{s.qualified}</td>
                          <td className="r num dim">{pct(s.rate, 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="footnote">
                    Relaxing the window barely helps, which separates the two explanations: this
                    is missing customer history, not a dataset too short to look back through.
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="grid g2" style={{ marginBottom: 12 }}>
        {a?.temporal && (
          <div className="card">
            <h3>Chargeback risk on real transactions · temporal split</h3>
            <div className="grid g2" style={{ marginBottom: 12 }}>
              <div className="stat"><div className="label">PR-AUC</div><div className="value teal">{a.temporal.pr_auc.toFixed(3)}</div><div className="sub">base rate {pct(a.temporal.base_rate, 2)} · {a.temporal.lift_over_base.toFixed(1)}× lift</div></div>
              <div className="stat"><div className="label">ROC-AUC</div><div className="value">{a.temporal.roc_auc.toFixed(3)}</div><div className="sub">{count(a.temporal.n)} unseen future transactions</div></div>
            </div>
            <table>
              <tbody>
                <tr><td>Best-F1 precision / recall</td><td className="r num">{a.temporal.best_f1.precision.toFixed(3)} / {a.temporal.best_f1.recall.toFixed(3)}</td></tr>
                <tr><td>Recall at precision ≥ 0.50</td><td className="r num">{a.temporal.at_precision_50.recall.toFixed(3)}</td></tr>
                {a.random_split && <tr><td className="dim">Random split (inflated)</td><td className="r num amber">{a.random_split.pr_auc.toFixed(3)}</td></tr>}
              </tbody>
            </table>
            {a.random_split && (
              <div className="callout" style={{ marginTop: 12 }}>
                A random split scores <strong>{a.random_split.pr_auc.toFixed(3)}</strong>; the same
                model scores <strong>{a.temporal.pr_auc.toFixed(3)}</strong> when it must predict
                forward in time. That gap is what a random split hides.
              </div>
            )}
            {a.contaminated_reference && (
              <div className="callout red" style={{ marginTop: 10 }}>
                <strong>Leakage check.</strong> Adding “did this customer have a prior chargeback”
                lifts PR-AUC to <strong>{a.contaminated_reference.pr_auc.toFixed(3)}</strong>. Vesta
                propagates the fraud label across an account's transactions, so that feature
                restates the answer. It is excluded, and the honest number is reported instead.
              </div>
            )}
          </div>
        )}

        {r && (
          <div className="card">
            <h3>Abuse rings · shared device fingerprint</h3>
            <div className="grid g2" style={{ marginBottom: 12 }}>
              <div className="stat"><div className="label">Chargeback rate in rings</div><div className="value red">{pct(r.chargeback_rate_in_rings)}</div><div className="sub">vs {pct(r.chargeback_rate_outside)} outside · {r.lift.toFixed(2)}× lift</div></div>
              <div className="stat"><div className="label">Value inside rings</div><div className="value amber">{inrCompact(r.chargeback_value_in_rings_inr)}</div><div className="sub">{count(r.n_rings)} rings · {count(r.n_entities_in_rings)} entities</div></div>
            </div>
            <table>
              <thead><tr><th>Ring</th><th className="r">Entities</th><th className="r">Devices</th><th className="r">CB rate</th><th className="r">Value</th></tr></thead>
              <tbody>
                {r.largest_rings.slice(0, 6).map((x) => (
                  <tr key={x.ring_id}>
                    <td className="mono" style={{ fontSize: 11.5 }}>#{x.ring_id}</td>
                    <td className="r num">{x.n_entities}</td>
                    <td className="r num dim">{x.n_devices}</td>
                    <td className="r num" style={{ color: x.chargeback_rate > 0.5 ? "var(--red)" : "var(--amber)" }}>{pct(x.chargeback_rate, 0)}</td>
                    <td className="r num">{inrCompact(x.chargeback_value_inr)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="footnote">{r.method}</div>
          </div>
        )}
      </div>

      {b?.held_out && (
        <div className="card">
          <h3>Evidence forensics on REAL receipt photographs</h3>
          <div className="grid g4" style={{ marginBottom: 12 }}>
            <div className="stat"><div className="label">Precision</div><div className="value teal">{pct(b.held_out.precision)}</div></div>
            <div className="stat"><div className="label">Recall</div><div className={b.held_out.recall < 0.77 ? "value amber" : "value teal"}>{pct(b.held_out.recall)}</div><div className="sub">human 77.0%</div></div>
            <div className="stat"><div className="label">False-positive rate</div><div className="value teal">{pct(b.held_out.fpr)}</div><div className="sub">human 12.0%</div></div>
            <div className="stat"><div className="label">PR-AUC</div><div className="value">{b.held_out.pr_auc.toFixed(3)}</div></div>
          </div>
          <div className="callout" style={{ borderLeftColor: "var(--amber)" }}>
            <strong>This is the number that matters, and it is worse than the synthetic one.</strong>{" "}
            On our own rendered fakes the detector reached 97.3% recall. On real photographs with
            real copy-move and splice manipulations it reaches {pct(b.held_out.recall)} — below the
            77.0% human baseline. Rendered fakes carried broken arithmetic and impossibly regular
            typography; a manipulation inside a genuine photograph carries neither. Precision stays
            high ({pct(b.held_out.precision)}) at a low false-positive rate ({pct(b.held_out.fpr)}),
            so what it flags is trustworthy — it simply misses more than half.
          </div>
          {b.per_family && (
            <div className="grid g2" style={{ marginTop: 14 }}>
              <div>
                <h3>Per family</h3>
                <table>
                  <tbody>
                    {Object.entries(b.per_family).map(([fam, x]: any) => {
                      const gen = fam === "genuine";
                      const v = gen ? x.false_positive_rate : x.recall;
                      return (
                        <tr key={fam}>
                          <td className="mono" style={{ fontSize: 11.5 }}>{fam}{gen && <span className="dimmer"> (FPR)</span>}</td>
                          <td className="r num dim">{x.n}</td>
                          <td className="r num">{pct(v)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div className="footnote">
                  <code>recycled</code> collapses because catching it needs the receipt total read
                  off a real photograph, and OCR on real receipts parses only a third of fields.
                </div>
              </div>
              <div>
                <h3>Ablations</h3>
                <table>
                  <thead><tr><th>Groups kept</th><th className="r">Recall</th><th className="r">PR-AUC</th></tr></thead>
                  <tbody>
                    {Object.entries(b.ablations ?? {}).map(([k, x]: any) => (
                      <tr key={k}>
                        <td className="mono" style={{ fontSize: 11 }}>{k}</td>
                        <td className="r num">{pct(x.recall)}</td>
                        <td className="r num dim">{x.pr_auc.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="footnote">
                  Dropping compression forensics costs the most — on real photographs the JPEG
                  history is the signal, where on rendered fakes it was arithmetic.
                </div>
              </div>
            </div>
          )}
          {m.adversarial && (
            <div style={{ marginTop: 14 }}>
              <h3>Adversarial robustness — recall when the forger fights back</h3>
              <div className="scroll-x">
                <table>
                  <thead><tr><th>Countermeasure</th><th className="r">Recall</th><th className="r">Δ</th><th>What it destroys</th></tr></thead>
                  <tbody>
                    {Object.entries(m.adversarial.attacks ?? {}).map(([k, x]: any) => {
                      const d = x.recall - m.adversarial.baseline_recall;
                      return (
                        <tr key={k}>
                          <td className="mono" style={{ fontSize: 11.5 }}>{k}</td>
                          <td className="r num">{pct(x.recall)}</td>
                          <td className="r num" style={{ color: d < -0.02 ? "var(--red)" : "var(--text-3)" }}>
                            {k === "none" ? "—" : `${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}pt`}
                          </td>
                          <td className="dim" style={{ fontSize: 11 }}>
                            {ATTACK_EFFECT[k] ?? ""}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="footnote">{m.adversarial.note}</div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
