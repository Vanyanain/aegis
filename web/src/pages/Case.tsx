import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get, inr, pct } from "../api";

type Txn = {
  txn_id: string; ts: string; amount_inr: number; product_description: string | null;
  merchandise_or_services: string | null; descriptor_text: string | null; channel: string | null;
  elements: Record<string, string | null>;
};
type Match = { element: string; label: string; tier: string; matched: boolean; missing_on: string[] };
type Gap = { code: string; detail: string; remediation: string; element: string | null };
type Qual = {
  qualified: boolean; rule_version: string; matched_elements: string[];
  matched_element_labels: string[]; candidate_prior_count: number;
  best_prior_pair: { prior_ids: string[]; main_matched: string[]; secondary_matched: string[]; matches: Match[] } | null;
  blocking_gaps: Gap[]; remediation: string[]; unlock_elements: string[];
  unlock_element_labels: string[]; naive_rule_qualified: boolean; naive_rule_disagrees: boolean;
  disputed_transaction: Txn; prior_transactions?: Txn[];
};
type Flag = { code: string; severity: string; detail: string };
type Ev = {
  item_id: string; label: string; driver: string; authenticity_score: number; tamper_score: number;
  flags: Flag[]; highest_severity: string; explanation: string; corroborated: boolean;
  top_features: { feature: string; label: string; value: number }[];
  _ground_truth_family: string;
};
type C = {
  dispute_id: string; txn_id: string; reason_code: string; dispute_date: string; amount_inr: number;
  win_prob: number; break_even: number; worth_fighting: boolean; expected_recovery_inr: number;
  intent_top: string; intent: Record<string, number>; qualification: Qual; evidence: Ev | null;
  recommendation: { action: string; tone: string; rationale: string; confidence: number };
  explanation: { feature: string; contribution: number; value: unknown }[];
  descriptor_is_clear: boolean; tc40_reported: boolean;
};

const VERDICT_STYLE: Record<string, { cls: string; color: string; border: string }> = {
  VERIFIED: { cls: "teal", color: "var(--teal)", border: "var(--teal-dim)" },
  REVIEW: { cls: "amber", color: "var(--amber)", border: "var(--amber-dim)" },
  SUSPECT: { cls: "amber", color: "var(--amber)", border: "var(--amber-dim)" },
  TAMPERED: { cls: "red", color: "var(--red)", border: "var(--red-dim)" },
};

const ACTION_STYLE: Record<string, string> = {
  REPRESENT_CE3: "ok", REPRESENT_STANDARD: "ok", ESCALATE_FORENSIC: "bad",
  SOFT_REFUND: "warn", ACCEPT_LOSS: "neutral",
};

function EvidenceImage({ itemId }: { itemId: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div style={{ border: "1px dashed var(--line-2)", borderRadius: 4, padding: "22px 14px", textAlign: "center", marginBottom: 12 }}>
        <div className="mono dimmer" style={{ fontSize: 11 }}>{itemId}</div>
        <div className="dim" style={{ fontSize: 11.5, marginTop: 6 }}>
          Image not bundled in this deployment. The forensic verdict below was computed from
          this file and is unaffected.
        </div>
      </div>
    );
  }
  return (
    <img
      className="evimg"
      src={`/api/evidence/${itemId}/image`}
      alt="customer-submitted evidence"
      style={{ marginBottom: 12 }}
      onError={() => setFailed(true)}
    />
  );
}

export default function Case() {
  const { id } = useParams();
  const [c, setC] = useState<C | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setC(null);
    get<C>(`/api/disputes/${id}`).then(setC).catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <div className="loading red">{err}</div>;
  if (!c) return <div className="loading">loading case…</div>;

  const q = c.qualification;
  const ev = c.evidence;
  const vs = ev ? VERDICT_STYLE[ev.label] ?? VERDICT_STYLE.REVIEW : null;
  const txns: Txn[] = [q.disputed_transaction, ...(q.prior_transactions ?? [])];

  return (
    <>
      <div className="page-head">
        <Link to="/console/disputes" className="dimmer mono" style={{ fontSize: 11 }}>← queue</Link>
        <h2 style={{ marginTop: 6 }}>
          <span className="mono">{c.dispute_id}</span>{" "}
          <span className="dim" style={{ fontWeight: 400, fontSize: 15 }}>
            · RC {c.reason_code} · {inr(c.amount_inr, 2)}
          </span>
        </h2>
        <p>Raised {c.dispute_date}. Transaction <code>{c.txn_id}</code>.</p>
      </div>

      {/* Recommendation first: it is the answer the analyst opened this page for. */}
      <div className="card" style={{ marginBottom: 12, borderLeft: `2px solid var(--${ACTION_STYLE[c.recommendation.action] === "bad" ? "red" : ACTION_STYLE[c.recommendation.action] === "ok" ? "teal" : "amber"})` }}>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "flex-start" }}>
          <div style={{ minWidth: 200 }}>
            <div className="dimmer" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.09em", fontWeight: 600 }}>Recommended action</div>
            <div className="mono" style={{ fontSize: 19, fontWeight: 700, marginTop: 5 }}>
              <span className={ACTION_STYLE[c.recommendation.action] === "bad" ? "red" : ACTION_STYLE[c.recommendation.action] === "ok" ? "teal" : "amber"}>
                {c.recommendation.action.replace(/_/g, " ")}
              </span>
            </div>
            <div className="dim" style={{ fontSize: 11.5, marginTop: 3 }}>tone: {c.recommendation.tone}</div>
          </div>
          <div style={{ flex: 1, minWidth: 320, fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.6 }}>
            {c.recommendation.rationale}
          </div>
          <dl className="kv" style={{ minWidth: 210 }}>
            <dt>Win probability</dt><dd>{pct(c.win_prob)}</dd>
            <dt>Break-even for this value</dt><dd>{pct(Math.min(c.break_even, 1))}</dd>
            <dt>Expected recovery</dt><dd className="teal">{inr(c.expected_recovery_inr)}</dd>
          </dl>
        </div>
      </div>

      {/* The two sides of the evidence war, literally side by side. */}
      <div className="split">
        {/* ------------------------------- SIDE A ------------------------------- */}
        <div className="card">
          <div className="side-head">
            <span className="n">A</span>
            <h3>Can we defend this?</h3>
            <span className="sub mono">{q.rule_version}</span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 13 }}>
            {q.qualified
              ? <span className="pill ok" style={{ fontSize: 11, padding: "4px 10px" }}>CE 3.0 QUALIFIED</span>
              : <span className="pill warn" style={{ fontSize: 11, padding: "4px 10px" }}>DOES NOT QUALIFY</span>}
            <span className="dim" style={{ fontSize: 11.5 }}>
              {q.candidate_prior_count} eligible prior transaction{q.candidate_prior_count === 1 ? "" : "s"}
            </span>
          </div>

          {q.naive_rule_disagrees && (
            <div className="callout red" style={{ marginBottom: 13 }}>
              <strong>A naive “any 2 of 4” implementation would call this winnable.</strong> The
              matched elements are Secondary only — with no IP or device anchor, Visa rejects it.
              Fighting this would cost the fight fee and lose.
            </div>
          )}

          {txns.length > 1 && (
            <div className="scroll-x" style={{ marginBottom: 13 }}>
              <table className="elgrid">
                <thead>
                  <tr>
                    <th>Element</th>
                    <th className="r">Disputed</th>
                    {(q.prior_transactions ?? []).map((t) => (
                      <th key={t.txn_id} className="r">{t.txn_id.slice(-5)}</th>
                    ))}
                    <th className="r">Match</th>
                  </tr>
                </thead>
                <tbody>
                  {(q.best_prior_pair?.matches ?? []).map((m) => {
                    const fields = m.element === "device_fp_or_id"
                      ? ["device_fingerprint", "device_id"]
                      : [m.element];
                    const cell = (t: Txn) => {
                      const v = fields.map((f) => t.elements[f]).find(Boolean);
                      return v ? <span className="teal">●</span> : <span className="dimmer">○</span>;
                    };
                    return (
                      <tr key={m.element}>
                        <td>
                          {m.label}
                          <span className={`tier ${m.tier}`}>{m.tier}</span>
                        </td>
                        <td className="r">{cell(q.disputed_transaction)}</td>
                        {(q.prior_transactions ?? []).map((t) => <td key={t.txn_id} className="r">{cell(t)}</td>)}
                        <td className="r mark">
                          {m.matched ? <span className="teal">✓</span> : <span className="dimmer">·</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div className="footnote">
                ● captured on that transaction · ✓ same value across all three. Qualifying needs
                <strong> two Main</strong>, or <strong>one Main + one Secondary</strong>.
              </div>
            </div>
          )}

          {q.blocking_gaps.length > 0 && (
            <div style={{ marginBottom: 13 }}>
              {q.blocking_gaps.map((g) => (
                <div className="flag medium" key={g.code}>
                  <div className="code amber">{g.code}</div>
                  <div className="detail">{g.detail}</div>
                </div>
              ))}
            </div>
          )}

          {q.unlock_element_labels.length > 0 && (
            <div className="callout teal">
              <strong>One field would flip this case:</strong>{" "}
              {q.unlock_element_labels.join(" or ")}.
              {q.remediation[0] ? <div style={{ marginTop: 6 }}>{q.remediation[0]}</div> : null}
            </div>
          )}

          {q.qualified && (
            <details style={{ marginTop: 12 }}>
              <summary className="dimmer mono" style={{ fontSize: 11, cursor: "pointer" }}>
                why this probability — top model contributions
              </summary>
              <table style={{ marginTop: 8 }}>
                <tbody>
                  {c.explanation.slice(0, 6).map((e) => (
                    <tr key={e.feature}>
                      <td className="mono" style={{ fontSize: 11 }}>{e.feature}</td>
                      <td className="r num" style={{ color: e.contribution > 0 ? "var(--teal)" : "var(--amber)" }}>
                        {e.contribution > 0 ? "+" : ""}{e.contribution.toFixed(3)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>

        {/* ------------------------------- SIDE B ------------------------------- */}
        <div className="card">
          <div className="side-head">
            <span className="n">B</span>
            <h3>Is their evidence real?</h3>
            {ev && <span className="sub mono">{ev.item_id}</span>}
          </div>

          {!ev ? (
            <div className="empty">
              No supporting evidence was submitted with this dispute.
              <div className="footnote" style={{ maxWidth: 340, margin: "10px auto 0" }}>
                Side B runs when a customer submits proof — a receipt, a delivery photo, a
                screenshot. Most 10.4 fraud claims come with none.
              </div>
            </div>
          ) : (
            <>
              <div className="verdict-banner" style={{ borderColor: vs!.border, background: `color-mix(in srgb, ${vs!.color} 7%, transparent)` }}>
                <span className="big" style={{ color: vs!.color }}>{ev.label}</span>
                <div style={{ flex: 1 }}>
                  <div className="dimmer" style={{ fontSize: 10 }}>AUTHENTICITY</div>
                  <div className="num" style={{ fontSize: 15, fontWeight: 700 }}>{pct(ev.authenticity_score)}</div>
                </div>
                <span className={`pill ${ev.driver === "rule" ? "bad" : "neutral"}`}>
                  {ev.driver === "rule" ? "deterministic rule" : "forensic model"}
                </span>
              </div>

              {/* Only a subset of the corpus ships in the deployed image, so a missing file
                  is expected rather than an error. Say so plainly instead of showing a
                  broken image icon — the verdict below is computed from cached features
                  and remains valid either way. */}
              <EvidenceImage itemId={ev.item_id} />

              <div style={{ fontSize: 12, color: "var(--text-2)", lineHeight: 1.6, marginBottom: 12 }}>
                {ev.explanation}
              </div>

              {ev.flags.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  {ev.flags.map((f) => (
                    <div className={`flag ${f.severity === "medium" || f.severity === "low" ? "medium" : ""}`} key={f.code}>
                      <div className={`code ${f.severity === "medium" || f.severity === "low" ? "amber" : "red"}`}>
                        {f.code} <span className="dimmer">· {f.severity}</span>
                      </div>
                      <div className="detail">{f.detail}</div>
                    </div>
                  ))}
                </div>
              )}

              <details>
                <summary className="dimmer mono" style={{ fontSize: 11, cursor: "pointer" }}>
                  forensic measurements
                </summary>
                <table style={{ marginTop: 8 }}>
                  <tbody>
                    {ev.top_features.map((f) => (
                      <tr key={f.feature}>
                        <td style={{ fontSize: 11.5 }}>{f.label}</td>
                        <td className="r num">{f.value.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>

              <div className="footnote">
                Ground truth for this demo item: <code>{ev._ground_truth_family}</code> — shown only
                so you can check the verdict. It is not an input to it.
              </div>
            </>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <h3>Genuine-intent read</h3>
        <div className="grid g3">
          {Object.entries(c.intent).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <div key={k}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, marginBottom: 4 }}>
                <span className={k === c.intent_top ? "" : "dim"}>{k.replace(/_/g, " ")}</span>
                <span className="num">{pct(v, 0)}</span>
              </div>
              <div className="bar">
                <span style={{ width: pct(v, 0), background: k === "criminal_fraud" ? "var(--violet)" : k === "genuine_service_failure" ? "var(--blue)" : "var(--amber)" }} />
              </div>
            </div>
          ))}
        </div>
        <div className="footnote">
          Intent sets the <em>tone</em>, not just the decision. A confused customer gets a refund; a
          repeat abuser gets a firm representment; a genuine fraud victim is never pursued.
        </div>
      </div>
    </>
  );
}
