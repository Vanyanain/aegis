import { useEffect, useState } from "react";
import { get } from "../api";

type Status = {
  stripe: { configured: boolean; mode: string | null; warning: string | null; scopes_needed: string[]; writes_to_stripe: boolean };
  supabase: { configured: boolean; project: string | null; warning: string | null; tables: string[]; bucket: string };
  rag: { configured: boolean; corpus_passages: number; note: string };
  setup: Record<string, string>;
};
type StripeDispute = {
  dispute_id: string; amount: number; currency: string; reason: string;
  network_reason_code: string | null; status: string; created: string;
  ce3_status: string | null; ce3_required_action_labels: string[];
  ce3_eligible_reason_code: boolean;
};
type Compare = {
  stripe_dispute: StripeDispute;
  aegis: { qualified: boolean; blocking_gaps: { code: string; detail: string }[]; unlock_element_labels: string[] };
  comparison: { agreement: string; note: string; what_aegis_adds: string; stripe_required_actions: string[]; aegis_blocking_gaps: string[] };
  prior_charges_seen: number;
};

const CE3_PILL: Record<string, string> = {
  qualified: "ok", requires_action: "warn", not_qualified: "bad",
};

export default function Integrations() {
  const [s, setS] = useState<Status | null>(null);
  const [disputes, setDisputes] = useState<StripeDispute[] | null>(null);
  const [cmp, setCmp] = useState<Compare | null>(null);
  const [err, setErr] = useState("");
  const [sql, setSql] = useState("");

  useEffect(() => { get<Status>("/api/integrations").then(setS).catch((e) => setErr(String(e))); }, []);

  useEffect(() => {
    if (s?.stripe.configured) {
      get<{ items: StripeDispute[] }>("/api/stripe/disputes?limit=20")
        .then((d) => setDisputes(d.items))
        .catch((e) => setErr(String(e)));
    }
  }, [s?.stripe.configured]);

  if (err && !s) return <div className="loading red">{err}</div>;
  if (!s) return <div className="loading">loading integrations…</div>;

  return (
    <>
      <div className="page-head">
        <h2>Connections</h2>
        <p>
          AEGIS reads from your payment processor and persists its evidence chain. Nothing here
          ever writes to Stripe — the packet builder assembles evidence for a human to submit.
        </p>
      </div>

      <div className="grid g3" style={{ marginBottom: 14 }}>
        <ConnCard
          name="Stripe"
          on={s.stripe.configured}
          detail={s.stripe.configured ? `${s.stripe.mode} mode · read-only` : "Not connected"}
          body={
            s.stripe.configured
              ? "Live disputes, with Stripe's own CE 3.0 eligibility verdict, checked against the AEGIS rulebook."
              : s.setup.stripe
          }
          warning={s.stripe.warning}
        />
        <ConnCard
          name="Supabase"
          on={s.supabase.configured}
          detail={s.supabase.configured ? `project ${s.supabase.project}` : "Not connected — in-memory only"}
          body={
            s.supabase.configured
              ? "Hash-chained evidence log, analyst decisions and uploaded evidence persist across restarts."
              : s.setup.supabase
          }
          warning={s.supabase.warning}
        />
        <ConnCard
          name="Rulebook retrieval"
          on={s.rag.configured}
          detail={`${s.rag.corpus_passages} cited passages`}
          body={s.rag.note}
          warning={null}
        />
      </div>

      {!s.stripe.configured && (
        <div className="card" style={{ marginBottom: 14 }}>
          <h3>Why Stripe is the integration that matters</h3>
          <div style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.7, maxWidth: "82ch" }}>
            Stripe implements Compelling Evidence 3.0 natively — a dispute carries an
            <code style={{ margin: "0 4px" }}>enhanced_eligibility</code> object whose status is
            <code style={{ margin: "0 4px" }}>qualified</code>,
            <code style={{ margin: "0 4px" }}>requires_action</code> or
            <code style={{ margin: "0 4px" }}>not_qualified</code>. That makes it an
            <strong> independent oracle</strong> for exactly the determination this product makes.
            Connecting it isn't "we can read your disputes" — it's running two implementations of
            the same published rule against the same case and showing where they differ.
          </div>
          <div className="callout info" style={{ marginTop: 14 }}>
            <strong>Stripe tells you the submission is incomplete. AEGIS tells you the case was
            lost 120 days ago, and which field decided it.</strong> Any disagreement between the
            two is a bug in one of them — which is a test worth having before a representment is
            filed on the strength of it.
          </div>
          <div className="footnote">
            Use a restricted key with read-only access to Disputes and Charges. AEGIS never
            writes to Stripe.
          </div>
        </div>
      )}

      {disputes && (
        <div className="card" style={{ marginBottom: 14 }}>
          <h3>Live Stripe disputes · {disputes.length}</h3>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Dispute</th><th>Reason</th><th className="r">Amount</th>
                  <th>RC</th><th>Stripe CE 3.0</th><th>Missing</th><th></th>
                </tr>
              </thead>
              <tbody>
                {disputes.map((d) => (
                  <tr key={d.dispute_id}>
                    <td className="mono" style={{ fontSize: 12 }}>{d.dispute_id}</td>
                    <td className="dim" style={{ fontSize: 12.5 }}>{d.reason}</td>
                    <td className="r num">{d.currency} {d.amount.toFixed(2)}</td>
                    <td className="mono dim" style={{ fontSize: 12 }}>{d.network_reason_code ?? "—"}</td>
                    <td>
                      {d.ce3_status
                        ? <span className={`pill ${CE3_PILL[d.ce3_status] ?? "neutral"}`}>{d.ce3_status.replace(/_/g, " ")}</span>
                        : <span className="dimmer">not assessed</span>}
                    </td>
                    <td className="dim" style={{ fontSize: 11.5 }}>
                      {d.ce3_required_action_labels.slice(0, 1).join("") || "—"}
                    </td>
                    <td className="r">
                      <button
                        className="btn"
                        style={{ padding: "4px 12px", fontSize: 12 }}
                        onClick={() =>
                          get<Compare>(`/api/stripe/disputes/${d.dispute_id}/compare`)
                            .then(setCmp).catch((e) => setErr(String(e)))
                        }
                      >Compare</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {cmp && (
        <div className="card" style={{ borderLeft: `3px solid var(--${cmp.comparison.agreement === "disagree" ? "red" : "teal"})` }}>
          <h3>Stripe vs AEGIS · {cmp.stripe_dispute.dispute_id}</h3>
          <div className="split">
            <div>
              <div className="side-head"><span className="n">S</span><h3>Stripe says</h3></div>
              <div style={{ marginBottom: 12 }}>
                <span className={`pill ${CE3_PILL[cmp.stripe_dispute.ce3_status ?? ""] ?? "neutral"}`}
                      style={{ fontSize: 12, padding: "5px 12px" }}>
                  {cmp.stripe_dispute.ce3_status ?? "not assessed"}
                </span>
              </div>
              {cmp.comparison.stripe_required_actions.length > 0 ? (
                cmp.comparison.stripe_required_actions.map((a) => (
                  <div className="flag medium" key={a}><div className="detail">{a}</div></div>
                ))
              ) : <div className="dim" style={{ fontSize: 13 }}>No outstanding submission requirements.</div>}
              <div className="footnote">Stripe assesses the evidence you are about to submit.</div>
            </div>
            <div>
              <div className="side-head"><span className="n">A</span><h3>AEGIS says</h3></div>
              <div style={{ marginBottom: 12 }}>
                <span className={`pill ${cmp.aegis.qualified ? "ok" : "warn"}`} style={{ fontSize: 12, padding: "5px 12px" }}>
                  {cmp.aegis.qualified ? "QUALIFIED" : "NOT QUALIFIED"}
                </span>
                <span className="dim" style={{ fontSize: 12, marginLeft: 10 }}>
                  {cmp.prior_charges_seen} prior charges seen
                </span>
              </div>
              {cmp.aegis.blocking_gaps.map((g) => (
                <div className="flag medium" key={g.code}>
                  <div className="code amber">{g.code}</div>
                  <div className="detail">{g.detail}</div>
                </div>
              ))}
              {cmp.aegis.unlock_element_labels.length > 0 && (
                <div className="callout teal">
                  <strong>One field would flip it:</strong> {cmp.aegis.unlock_element_labels.join(" or ")}.
                </div>
              )}
            </div>
          </div>
          <div className={`callout ${cmp.comparison.agreement === "disagree" ? "red" : "teal"}`} style={{ marginTop: 16 }}>
            <strong>{cmp.comparison.agreement.replace(/_/g, " ").toUpperCase()}.</strong>{" "}
            {cmp.comparison.note}
          </div>
          <div className="footnote">{cmp.comparison.what_aegis_adds}</div>
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <h3>Supabase schema</h3>
        <div style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.65, marginBottom: 12, maxWidth: "82ch" }}>
          The Section 63 BSA certificate rests on a hash chain. A chain that lives in container
          memory restarts at genesis whenever Cloud Run recycles the instance — which is exactly
          the property the certificate is supposed to rule out. The table below is append-only by
          policy: an <code>UPDATE</code> could otherwise rewrite history while leaving the chain
          internally consistent.
        </div>
        {sql ? (
          <pre style={{
            background: "var(--surface-3)", border: "1px solid var(--line)", borderRadius: 10,
            padding: 16, overflowX: "auto", fontSize: 11.5, lineHeight: 1.6,
            fontFamily: "var(--mono)", color: "var(--ink-2)",
          }}>{sql}</pre>
        ) : (
          <button className="btn" onClick={() =>
            get<{ sql: string }>("/api/supabase/schema").then((d) => setSql(d.sql)).catch((e) => setErr(String(e)))
          }>Show setup SQL</button>
        )}
      </div>
    </>
  );
}

function ConnCard({ name, on, detail, body, warning }: {
  name: string; on: boolean; detail: string; body: string; warning: string | null;
}) {
  return (
    <div className="card">
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 10 }}>
        <span style={{
          width: 8, height: 8, borderRadius: "50%",
          background: on ? "var(--teal)" : "var(--line-2)", flex: "none",
        }} />
        <span style={{ fontSize: 15, fontWeight: 600 }}>{name}</span>
        <span className={`pill ${on ? "ok" : "neutral"}`} style={{ marginLeft: "auto" }}>
          {on ? "connected" : "not connected"}
        </span>
      </div>
      <div className="mono dimmer" style={{ fontSize: 11.5, marginBottom: 10 }}>{detail}</div>
      <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>{body}</div>
      {warning && <div className="callout red" style={{ marginTop: 12, fontSize: 12.5 }}>{warning}</div>}
    </div>
  );
}
