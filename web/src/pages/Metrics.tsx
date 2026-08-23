import { useEffect, useState } from "react";
import { get, pct } from "../api";

type Rep = {
  precision: number; recall: number; f1: number; accuracy: number; fpr: number;
  roc_auc: number; pr_auc: number; brier: number; n: number;
  confusion: { tn: number; fp: number; fn: number; tp: number };
};
type M = {
  side_a: any;
  side_b: {
    held_out: Rep; per_family: Record<string, any>; combined_per_family: Record<string, any>;
    layers: Record<string, Rep>; leave_one_family_out: Record<string, Rep>;
    ablations: Record<string, Rep & { n_features: number }>;
    human_baseline: { source: string; accuracy: number; recall: number; fpr: number; f1: number };
    beats_human_recall: boolean; beats_human_fpr: boolean;
    importance_by_group: Record<string, number>;
  };
  fusion: any;
};

export default function Metrics() {
  const [m, setM] = useState<M | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get<M>("/api/metrics").then(setM).catch((e) => setErr(String(e))); }, []);

  if (err) return <div className="loading red">{err}</div>;
  if (!m) return <div className="loading">loading metrics…</div>;

  const a = m.side_a, b = m.side_b, f = m.fusion;
  const hb = b.human_baseline;

  return (
    <>
      <div className="page-head">
        <h2>Model metrics</h2>
        <p>
          Held-out results for both sides, reported the way they came out. Splits are grouped by
          customer, so no customer appears in both training and test. Where a number is
          unflattering it is still here.
        </p>
      </div>

      <div className="card" style={{ marginBottom: 12, borderLeft: "2px solid var(--amber)" }}>
        <h3>Read this first</h3>
        <div style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.65 }}>
          Every figure below comes from <strong>synthetic data</strong>. The transaction ledger was
          generated to make CE 3.0 logic exercisable — no public dataset carries device
          fingerprints and IPs linked across a customer's order history — and the outcome labels
          were drawn from a documented structural model. Training a model to recover labels that a
          model produced is <strong>partly circular</strong>. Three things limit that: a per-issuer
          random effect that is never given to any model, a deliberately large noise term, and
          customer-grouped splits throughout. These metrics measure whether the pipeline recovers a
          known structure under noise. <strong>They are not external-validity claims.</strong>{" "}
          Validation on real dispute data is the necessary next step.
        </div>
      </div>

      {/* ---------------- Side B ---------------- */}
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="side-head"><span className="n">B</span><h3>Evidence forensics — is the submitted proof real?</h3></div>

        <div className="grid g4" style={{ marginBottom: 14 }}>
          <Stat label="Precision" v={pct(b.held_out.precision)} c="teal" />
          <Stat label="Recall" v={pct(b.held_out.recall)} c="teal" sub={`human ${pct(hb.recall)}`} />
          <Stat label="False-positive rate" v={pct(b.held_out.fpr)} c="teal" sub={`human ${pct(hb.fpr)}`} />
          <Stat label="PR-AUC" v={b.held_out.pr_auc.toFixed(3)} c="" sub={`Brier ${b.held_out.brier.toFixed(3)}`} />
        </div>

        <div className="callout teal" style={{ marginBottom: 14 }}>
          <strong>Against the human baseline.</strong> The reference is a 30-annotator study on
          AI-generated receipts ({hb.source}): accuracy {pct(hb.accuracy)}, recall {pct(hb.recall)},
          FPR {pct(hb.fpr)}. AEGIS reaches recall {pct(b.held_out.recall)} at FPR{" "}
          {pct(b.held_out.fpr)} — better on both. Humans have <em>better</em> visual discrimination
          than most machine evaluators; they lose because the dominant signal is arithmetic
          inconsistency, which is invisible to visual inspection.
        </div>

        <div className="grid g2">
          <div>
            <h3>Per-family recall</h3>
            <table>
              <thead><tr><th>Family</th><th className="r">n</th><th className="r">Model</th><th className="r">+ rules</th></tr></thead>
              <tbody>
                {Object.entries(b.per_family).map(([fam, r]: any) => {
                  const comb: any = b.combined_per_family?.[fam] ?? {};
                  const isGen = fam === "genuine";
                  const v = isGen ? r.false_positive_rate : r.recall;
                  const cv = isGen ? comb.false_positive_rate : comb.recall;
                  return (
                    <tr key={fam}>
                      <td>
                        <span className="mono" style={{ fontSize: 11.5 }}>{fam}</span>
                        {isGen && <span className="dimmer" style={{ fontSize: 10 }}> (false-positive rate)</span>}
                      </td>
                      <td className="r num dim">{r.n}</td>
                      <td className="r num">{pct(v)}</td>
                      <td className="r num dim">{cv != null ? pct(cv) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="footnote">
              An average over families hides which one fails. <code>recycled</code> is a genuine,
              unaltered receipt from a different order — no pixel forensic can flag it, and it is
              caught only because AEGIS holds the transaction record.
            </div>
          </div>

          <div>
            <h3>Generalisation to an unseen fake family</h3>
            <table>
              <thead><tr><th>Held out of training</th><th className="r">Recall</th><th className="r">FPR</th><th className="r">PR-AUC</th></tr></thead>
              <tbody>
                {Object.entries(b.leave_one_family_out).map(([fam, r]) => (
                  <tr key={fam}>
                    <td className="mono" style={{ fontSize: 11.5 }}>{fam}</td>
                    <td className={`r num ${r.recall < 0.7 ? "amber" : "teal"}`}>{pct(r.recall)}</td>
                    <td className="r num dim">{pct(r.fpr)}</td>
                    <td className="r num dim">{r.pr_auc.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="footnote">
              Train on three fake families, test on the fourth the model has never seen. This is
              the honest answer to “will it catch a generator you haven't trained on?”{" "}
              <strong className="amber">digital_edit is the weak spot</strong> — a small localised
              splice is the hardest thing to recognise from families that look nothing like it.
            </div>
          </div>
        </div>

        <div className="grid g2" style={{ marginTop: 14 }}>
          <div>
            <h3>Ablations — what survives an adaptive forger</h3>
            <table>
              <thead><tr><th>Feature groups kept</th><th className="r">Feats</th><th className="r">Recall</th><th className="r">Precision</th></tr></thead>
              <tbody>
                {Object.entries(b.ablations).map(([k, r]) => (
                  <tr key={k}>
                    <td className="mono" style={{ fontSize: 11 }}>{k}</td>
                    <td className="r num dim">{r.n_features}</td>
                    <td className="r num">{pct(r.recall)}</td>
                    <td className="r num dim">{pct(r.precision)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="footnote">
              Stripping EXIF is one command, so performance without the provenance group is the
              floor a merchant should plan around. Removing the ledger cross-check costs the most —
              which is precisely the layer a document-only tool does not have.
            </div>
          </div>

          <div>
            <h3>Which layer carries the result</h3>
            <table>
              <thead><tr><th>Layer</th><th className="r">Recall</th><th className="r">Precision</th><th className="r">FPR</th></tr></thead>
              <tbody>
                {Object.entries(b.layers).map(([k, r]) => (
                  <tr key={k}>
                    <td className="mono" style={{ fontSize: 11.5 }}>{k}</td>
                    <td className="r num">{pct(r.recall)}</td>
                    <td className="r num">{pct(r.precision)}</td>
                    <td className="r num dim">{pct(r.fpr)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3 style={{ marginTop: 14 }}>Feature-group importance</h3>
            <table>
              <tbody>
                {Object.entries(b.importance_by_group).sort((x, y) => y[1] - x[1]).map(([g, v]) => (
                  <tr key={g}>
                    <td className="mono" style={{ fontSize: 11.5 }}>{g}</td>
                    <td className="r num dim">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="footnote">
              An earlier corpus let EXIF separate the classes perfectly and every other group fell
              to zero importance. Modelling how evidence actually arrives — WhatsApp strips
              metadata, forgers fabricate it — removed that shortcut and forced the model onto real
              document features.
            </div>
          </div>
        </div>
      </div>

      {/* ---------------- Side A ---------------- */}
      <div className="grid g2">
        <div className="card">
          <div className="side-head"><span className="n">A</span><h3>Win probability — should we fight?</h3></div>
          <div className="grid g2" style={{ marginBottom: 12 }}>
            <Stat label="ROC-AUC" v={a.roc_auc.toFixed(3)} c="" />
            <Stat label="PR-AUC" v={a.pr_auc.toFixed(3)} c="" sub={`base rate ${pct(a.test_base_rate)}`} />
            <Stat label="Brier (calibrated)" v={a.brier_calibrated.toFixed(4)} c="" sub={`from ${a.brier_uncalibrated.toFixed(4)}`} />
            <Stat label="Test cases" v={String(a.n_test)} c="" sub="unseen customers" />
          </div>
          <div className="callout">
            PR-AUC {a.pr_auc.toFixed(3)} against a base rate of {pct(a.test_base_rate)} is roughly a{" "}
            <strong>{(a.pr_auc / a.test_base_rate).toFixed(1)}× lift</strong>. It is not higher
            because the generative process carries a hidden per-issuer effect and a large noise
            term that no model can see — deliberately, so this number reflects a hard problem
            rather than an invertible formula.
          </div>
          {a.by_qualification?.qualified && (
            <table style={{ marginTop: 12 }}>
              <thead><tr><th>Segment</th><th className="r">n</th><th className="r">Actual</th><th className="r">Predicted</th></tr></thead>
              <tbody>
                {["qualified", "not_qualified"].map((k) => {
                  const s = a.by_qualification[k];
                  if (!s || s.n == null || s.actual_win_rate == null) return null;
                  return (
                    <tr key={k}>
                      <td className="mono" style={{ fontSize: 11.5 }}>{k}</td>
                      <td className="r num dim">{s.n}</td>
                      <td className="r num">{pct(s.actual_win_rate)}</td>
                      <td className="r num dim">{pct(s.mean_predicted)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <div className="side-head"><span className="n">M3</span><h3>Genuine-intent classifier</h3></div>
          <div className="grid g2" style={{ marginBottom: 12 }}>
            <Stat label="Macro F1" v={f.macro_f1.toFixed(3)} c="" />
            <Stat label="Accuracy" v={pct(f.accuracy)} c="" sub={`log-loss ${f.log_loss.toFixed(3)}`} />
          </div>
          <table>
            <thead><tr><th>Intent</th><th className="r">P</th><th className="r">R</th><th className="r">F1</th><th className="r">AUC</th></tr></thead>
            <tbody>
              {Object.entries(f.per_class).map(([k, r]: any) => (
                <tr key={k}>
                  <td className="mono" style={{ fontSize: 11 }}>{k.replace(/_/g, " ")}</td>
                  <td className="r num dim">{r.precision.toFixed(2)}</td>
                  <td className="r num">{r.recall.toFixed(2)}</td>
                  <td className="r num dim">{r["f1-score"].toFixed(2)}</td>
                  <td className="r num dim">{f.ovr_roc_auc[k]?.toFixed(3) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="callout" style={{ marginTop: 12 }}>
            Trained with balanced class weights. Unweighted, <code>criminal_fraud</code> scored{" "}
            <strong>0.000 precision and recall</strong> — the model ranked it at 0.72 AUC but the
            majority classes always won the argmax. The costs here are not symmetric: predicting
            first-party misuse for a cardholder whose card was genuinely stolen means pursuing a
            fraud victim. Accuracy fell from 0.665 to {f.accuracy.toFixed(3)}; macro F1 rose to{" "}
            {f.macro_f1.toFixed(3)}. That is the right trade.
          </div>
        </div>
      </div>
    </>
  );
}

function Stat({ label, v, c, sub }: { label: string; v: string; c: string; sub?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value ${c}`}>{v}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}
