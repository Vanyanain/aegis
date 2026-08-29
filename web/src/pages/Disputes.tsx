import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, inr, pct } from "../api";

export type Row = {
  dispute_id: string; txn_id: string; reason_code: string; dispute_date: string;
  amount_inr: number; qualified: boolean; n_matched: number; matched_elements: string[];
  primary_gap: string | null; win_prob: number; break_even: number; worth_fighting: boolean;
  expected_recovery_inr: number; intent_top: string; has_evidence: boolean;
  tc40_reported: boolean; category: string; channel: string; descriptor_is_clear: boolean;
};

const INTENT_SHORT: Record<string, string> = {
  first_party_misuse: "1st-party",
  criminal_fraud: "criminal",
  genuine_service_failure: "service fail",
};

export default function Disputes() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [only104, setOnly104] = useState(false);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  useEffect(() => {
    setRows(null);
    get<{ items: Row[] }>(`/api/disputes?limit=200&only_104=${only104}`)
      .then((d) => setRows(d.items))
      .catch((e) => setErr(String(e)));
  }, [only104]);

  if (err) return <div className="loading red">{err}</div>;

  return (
    <>
      <div className="page-head">
        <h2>Dispute queue</h2>
        <p>
          Ranked by rupees genuinely at stake — win probability × disputed amount — not by
          probability alone. A 90% chance on ₹300 deserves less attention than a 40% chance on
          ₹40,000.
        </p>
      </div>

      <div className="tabs">
        <button className={!only104 ? "on" : ""} onClick={() => setOnly104(false)}>All reason codes</button>
        <button className={only104 ? "on" : ""} onClick={() => setOnly104(true)}>10.4 only (CE 3.0 eligible)</button>
      </div>

      {!rows ? (
        <div className="loading">scoring cases…</div>
      ) : (
        <div className="card scroll-x" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Dispute</th>
                <th>RC</th>
                <th className="r">Amount</th>
                <th>CE 3.0</th>
                <th className="r">Win prob</th>
                <th className="r">Break-even</th>
                <th>Decision</th>
                <th>Intent</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.dispute_id} className="click" onClick={() => nav(`/console/disputes/${r.dispute_id}`)}>
                  <td>
                    <span className="mono" style={{ fontSize: 11.5 }}>{r.dispute_id}</span>
                    <div className="dimmer" style={{ fontSize: 10 }}>{r.dispute_date} · {r.category}</div>
                  </td>
                  <td><span className="mono dim" style={{ fontSize: 11 }}>{r.reason_code}</span></td>
                  <td className="r num" style={{ fontWeight: 600 }}>{inr(r.amount_inr)}</td>
                  <td>
                    {r.reason_code !== "10.4" ? (
                      <span className="pill neutral">n/a</span>
                    ) : r.qualified ? (
                      <span className="pill ok">qualified · {r.n_matched}</span>
                    ) : (
                      <span className="pill warn">no</span>
                    )}
                  </td>
                  <td className="r num">{pct(r.win_prob, 0)}</td>
                  <td className="r num dim">{pct(Math.min(r.break_even, 1), 0)}</td>
                  <td>
                    {/* The comparison that matters: probability against THIS case's own
                        break-even, which scales with the amount at stake. */}
                    {r.worth_fighting ? (
                      <span className="pill ok">fight</span>
                    ) : (
                      <span className="pill neutral">concede</span>
                    )}
                  </td>
                  <td className="dim" style={{ fontSize: 11.5 }}>{INTENT_SHORT[r.intent_top] ?? r.intent_top}</td>
                  <td>
                    {r.has_evidence ? <span className="pill warn">submitted</span> : <span className="dimmer">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
