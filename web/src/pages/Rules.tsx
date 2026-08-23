import { useEffect, useState } from "react";
import { get } from "../api";

type R = {
  manifest: { active: Record<string, string>; ce3: any[]; vamp: any[] };
  ce3: {
    version: string; effective_from: string; eligible_reason_codes: string[];
    prior_min_age_days: number; prior_max_age_days: number; min_prior_transactions: number;
    main_elements: { key: string; label: string }[];
    secondary_elements: { key: string; label: string }[];
    match_rule: string; common_misreading: string;
  };
  vamp: {
    version: string; effective_from: string; excessive_ratio: number;
    monthly_item_floor: number; fee_per_dispute_usd: number; regions: string[]; numerator: string;
  };
};

export default function Rules() {
  const [r, setR] = useState<R | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get<R>("/api/rules").then(setR).catch((e) => setErr(String(e))); }, []);

  if (err) return <div className="loading red">{err}</div>;
  if (!r) return <div className="loading">loading rulebook…</div>;

  return (
    <>
      <div className="page-head">
        <h2>Rulebook</h2>
        <p>
          Network rules live in versioned code with effective dates, not in model weights. When
          Visa moves a threshold, one file changes and nothing retrains — and a dispute is always
          judged under the rulebook in force when it was raised.
        </p>
      </div>

      <div className="grid g2">
        <div className="card">
          <h3>Compelling Evidence 3.0 · <span className="mono teal">{r.ce3.version}</span></h3>

          <div className="callout red" style={{ marginBottom: 14 }}>
            <strong>The tier rule, which is where implementations go wrong.</strong>{" "}
            {r.ce3.common_misreading}
          </div>

          <div className="grid g2" style={{ marginBottom: 14 }}>
            <div>
              <div className="dimmer" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Main elements</div>
              {r.ce3.main_elements.map((e) => (
                <div key={e.key} style={{ padding: "5px 9px", background: "var(--bg-2)", border: "1px solid #1e3a5f", borderRadius: 3, marginBottom: 5, fontSize: 11.5 }}>
                  <span className="blue">{e.label}</span>
                </div>
              ))}
              <div className="footnote">Fingerprint and device ID share one slot — matching both is not two elements.</div>
            </div>
            <div>
              <div className="dimmer" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>Secondary elements</div>
              {r.ce3.secondary_elements.map((e) => (
                <div key={e.key} style={{ padding: "5px 9px", background: "var(--bg-2)", border: "1px solid var(--line-2)", borderRadius: 3, marginBottom: 5, fontSize: 11.5 }}>
                  <span className="dim">{e.label}</span>
                </div>
              ))}
              <div className="footnote">Two Secondary elements never qualify on their own.</div>
            </div>
          </div>

          <div style={{ padding: "10px 13px", background: "var(--bg-2)", borderRadius: 4, border: "1px solid var(--line)", marginBottom: 14 }}>
            <div className="mono teal" style={{ fontSize: 12.5, fontWeight: 700 }}>
              qualified ⟺ 2 Main · or · 1 Main + 1 Secondary
            </div>
          </div>

          <dl className="kv">
            <dt>Eligible reason codes</dt><dd>{r.ce3.eligible_reason_codes.join(", ")}</dd>
            <dt>Prior transactions required</dt><dd>{r.ce3.min_prior_transactions}</dd>
            <dt>Prior age window</dt><dd>{r.ce3.prior_min_age_days}–{r.ce3.prior_max_age_days} days</dd>
            <dt>Effective from</dt><dd>{r.ce3.effective_from}</dd>
          </dl>

          <div className="footnote">
            Priors must also be settled, never disputed, never TC40-reported, not validation
            charges, and all three transactions need a product description and a
            merchandise/services classification.
          </div>
        </div>

        <div className="card">
          <h3>VAMP · <span className="mono teal">{r.vamp.version}</span></h3>
          <dl className="kv" style={{ marginBottom: 14 }}>
            <dt>Excessive threshold</dt><dd className="red">{(r.vamp.excessive_ratio * 100).toFixed(2)}% ({(r.vamp.excessive_ratio * 10000).toFixed(0)} bps)</dd>
            <dt>Monthly item floor</dt><dd>{r.vamp.monthly_item_floor.toLocaleString("en-IN")}</dd>
            <dt>Fee per dispute</dt><dd>${r.vamp.fee_per_dispute_usd.toFixed(2)}</dd>
            <dt>Regions</dt><dd>{r.vamp.regions.join(" · ")}</dd>
            <dt>Effective from</dt><dd>{r.vamp.effective_from}</dd>
          </dl>

          <div className="callout" style={{ marginBottom: 12 }}>
            <strong>Numerator:</strong> {r.vamp.numerator}. Because a fraud chargeback files both, a
            ratio computed as chargebacks ÷ transactions understates the real position by roughly
            half on the fraud portion.
          </div>

          <div className="callout teal">
            <strong>Below the {r.vamp.monthly_item_floor.toLocaleString("en-IN")}-item floor a
            merchant is not identified under VAMP at all</strong>, however bad the ratio looks.
            Small merchants often worry about a number that cannot be enforced against them; large
            merchants a few basis points over are facing real money.
          </div>

          <h3 style={{ marginTop: 18 }}>Loaded versions</h3>
          <table>
            <thead><tr><th>Rulebook</th><th>Version</th><th className="r">Effective from</th></tr></thead>
            <tbody>
              {[...r.manifest.ce3.map((v) => ["CE 3.0", v]), ...r.manifest.vamp.map((v) => ["VAMP", v])].map(([name, v]: any) => (
                <tr key={v.version}>
                  <td>{name}</td>
                  <td className="mono" style={{ fontSize: 11.5 }}>{v.version}</td>
                  <td className="r mono dim" style={{ fontSize: 11.5 }}>{v.effective_from}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: 12, borderLeft: "2px solid var(--teal)" }}>
        <h3>Evidence admissibility · India</h3>
        <div style={{ fontSize: 12.5, color: "var(--text-2)", lineHeight: 1.65 }}>
          Every forensic verdict is written to an append-only, SHA-256 hash-chained log, and each
          case can emit a certificate shaped for{" "}
          <strong className="teal">Section 63 of the Bharatiya Sakshya Adhiniyam, 2023</strong> — in
          force since 1 July 2024, replacing Section 65B of the repealed Indian Evidence Act, 1872.
          Section 63(4) requires <strong>two</strong> signatories: the officer responsible for the
          system, and an expert where an expert examination was undertaken. A forensic verdict is
          such an examination, so the certificate carries both blocks — and AEGIS generates it{" "}
          <strong>unsigned</strong>. The system records the facts and the hashes; the attestations
          are human acts. A tool that signed its own expert certificate would be worth nothing.
        </div>
      </div>
    </>
  );
}
