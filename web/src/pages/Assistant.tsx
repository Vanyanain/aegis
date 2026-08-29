import { useEffect, useRef, useState } from "react";
import { get, post } from "../api";

type Citation = {
  id: string; topic: string; text: string; source: string; url: string;
  score: number; lexical: number; semantic: number;
};
type Answer = {
  query: string; answered: boolean; message?: string;
  case_facts: string[]; citations: Citation[]; backend: string;
  grounding_note?: string;
};
type Suggestions = {
  questions: string[];
  corpus: { id: string; topic: string; source: string }[];
};

type Turn = { q: string; a: Answer | null; error?: string };

export default function Assistant() {
  const [sug, setSug] = useState<Suggestions | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [disputeId, setDisputeId] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { get<Suggestions>("/api/ask/suggestions").then(setSug).catch(() => {}); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  async function send(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true);
    setQ("");
    setTurns((t) => [...t, { q: text, a: null }]);
    try {
      const a = await post<Answer>("/api/ask", {
        question: text,
        dispute_id: disputeId.trim() || null,
      });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: String(e) } : x)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h2>Rulebook assistant</h2>
        <p>
          Ask about the CE 3.0 criteria, VAMP economics, evidence forensics or Indian
          admissibility. Answers are assembled from cited rulebook passages and, when you
          supply a dispute, that case's own computed facts — never written by a language model.
        </p>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 320px", gap: 14 }}>
        <div>
          <div className="card" style={{ minHeight: 380, display: "flex", flexDirection: "column" }}>
            {turns.length === 0 ? (
              <div className="empty" style={{ margin: "auto" }}>
                <div style={{ fontSize: 15, color: "var(--ink-2)", marginBottom: 8 }}>
                  Nothing asked yet.
                </div>
                <div style={{ fontSize: 13 }}>Pick a question on the right, or type one below.</div>
              </div>
            ) : (
              <div style={{ flex: 1 }}>
                {turns.map((t, i) => (
                  <div key={i} style={{ marginBottom: 26 }}>
                    <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
                      <div style={{
                        width: 24, height: 24, borderRadius: 7, flex: "none",
                        background: "var(--surface-3)", display: "grid", placeItems: "center",
                        fontSize: 11, fontWeight: 700, color: "var(--ink-3)",
                      }}>Q</div>
                      <div style={{ fontSize: 15, fontWeight: 550, paddingTop: 1 }}>{t.q}</div>
                    </div>

                    {t.error && <div className="callout red">{t.error}</div>}
                    {!t.a && !t.error && <div className="loading" style={{ padding: 20 }}>retrieving…</div>}

                    {t.a && !t.a.answered && (
                      <div className="callout">{t.a.message}</div>
                    )}

                    {t.a?.answered && (
                      <div style={{ paddingLeft: 34 }}>
                        {t.a.case_facts.length > 0 && (
                          <div className="callout info" style={{ marginBottom: 14 }}>
                            <div style={{ fontWeight: 600, color: "var(--ink)", marginBottom: 7, fontSize: 12.5 }}>
                              Computed for this case
                            </div>
                            {t.a.case_facts.map((f, j) => (
                              <div key={j} style={{ marginBottom: 5 }}>• {f}</div>
                            ))}
                          </div>
                        )}

                        {t.a.citations.map((c) => (
                          <div key={c.id} style={{
                            border: "1px solid var(--line)", borderRadius: 10,
                            padding: "14px 16px", marginBottom: 10, background: "var(--surface-2)",
                          }}>
                            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 7 }}>
                              <span style={{ fontWeight: 600, fontSize: 13.5 }}>{c.topic}</span>
                              <span className="mono dimmer" style={{ fontSize: 10.5, marginLeft: "auto" }}>
                                {c.score.toFixed(2)}
                              </span>
                            </div>
                            <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.65 }}>{c.text}</div>
                            <div className="footnote" style={{ marginTop: 9 }}>
                              {c.url ? (
                                <a href={c.url} target="_blank" rel="noreferrer" style={{ color: "var(--indigo)" }}>
                                  {c.source} ↗
                                </a>
                              ) : c.source}
                            </div>
                          </div>
                        ))}

                        {t.a.grounding_note && (
                          <div className="footnote" style={{ marginTop: 10 }}>{t.a.grounding_note}</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                <div ref={endRef} />
              </div>
            )}

            <form
              onSubmit={(e) => { e.preventDefault(); send(q); }}
              style={{ display: "flex", gap: 8, marginTop: 18, paddingTop: 18, borderTop: "1px solid var(--line)" }}
            >
              <input
                type="text"
                placeholder="Ask about CE 3.0, VAMP, evidence forensics, Section 63 BSA…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                style={{ fontFamily: "var(--sans)", fontSize: 14 }}
              />
              <button className="btn primary" type="submit" disabled={busy || !q.trim()}>
                {busy ? "…" : "Ask"}
              </button>
            </form>
          </div>
        </div>

        <div>
          <div className="card" style={{ marginBottom: 14 }}>
            <h3>Suggested</h3>
            {(sug?.questions ?? []).map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                style={{
                  display: "block", width: "100%", textAlign: "left",
                  padding: "9px 11px", marginBottom: 5, borderRadius: 8,
                  fontSize: 13, color: "var(--ink-2)", border: "1px solid var(--line)",
                  background: "var(--surface-2)", lineHeight: 1.45,
                }}
              >
                {s}
              </button>
            ))}
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <h3>Ground in a case</h3>
            <div className="field">
              <label>Dispute ID (optional)</label>
              <input
                type="text"
                placeholder="DSP000628"
                value={disputeId}
                onChange={(e) => setDisputeId(e.target.value)}
              />
              <div className="hint">
                Supply one and answers include that case's actual qualification result,
                forensic verdict and recommended action alongside the rulebook citations.
              </div>
            </div>
          </div>

          <div className="card">
            <h3>Corpus · {sug?.corpus.length ?? 0} passages</h3>
            <div style={{ maxHeight: 260, overflowY: "auto" }}>
              {(sug?.corpus ?? []).map((c) => (
                <div key={c.id} style={{ marginBottom: 9 }}>
                  <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>{c.topic}</div>
                  <div className="mono dimmer" style={{ fontSize: 10 }}>{c.id}</div>
                </div>
              ))}
            </div>
            <div className="footnote">
              Hand-curated, not scraped. In this domain a stale threshold is not a stale fact —
              it is a wrong decision about money.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
