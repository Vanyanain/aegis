import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";

/* The marketing page, built on Mercury's section rhythm: a tall hero with a moving product
 * shot, then alternating feature bands, a statistics band, and a closing call to action over
 * an inverted dark panel, with the honest caveats in the footer where a fintech site puts
 * its disclosures.
 *
 * The product demo is NOT a recorded screencast. It is an iframe of this same deployment
 * cycling through its own routes, so what a visitor watches is the live application against
 * live data. A recording of a dashboard starts lying the moment the dashboard changes, and
 * on a project whose entire argument is "measured, not asserted", shipping a staged video of
 * the product would undercut the thing being claimed.
 */

type Qual = {
  n_transactions: number;
  n_chargebacks: number;
  n_assessable: number;
  funnel: { cleared_prior_gate: number; qualified: number; blocked_no_main_anchor: number };
  naive_false_positives: number;
};
type Rings = { lift: number; n_rings: number };
type SideB = { held_out: { recall: number; precision: number; fpr: number } };

const SCREENS = [
  { path: "#/console", label: "Portfolio", blurb: "What this merchant can actually defend today." },
  { path: "#/console/real", label: "Real data", blurb: "Visa's gate run over 20,663 real chargebacks." },
  { path: "#/console/costlab", label: "Cost Lab", blurb: "The threshold chosen in rupees, not accuracy." },
  { path: "#/console/assistant", label: "Assistant", blurb: "Every answer cited to the rulebook." },
  { path: "#/console/metrics", label: "Metrics", blurb: "Including the numbers that disappoint." },
];

const DWELL_MS = 6500;

/* Petal positions and timings are fixed rather than random so the drift is identical on
 * every render. Randomising them re-seeds on each React re-render, which makes the whole
 * field visibly jump every time the demo tab advances. */
const PETALS = [
  { l: "4%", d: 13, delay: 0 },   { l: "12%", d: 17, delay: 3.5 },
  { l: "21%", d: 11, delay: 6 },  { l: "29%", d: 19, delay: 1.5 },
  { l: "38%", d: 14, delay: 8 },  { l: "47%", d: 16, delay: 4.5 },
  { l: "56%", d: 12, delay: 10 }, { l: "64%", d: 18, delay: 2 },
  { l: "73%", d: 15, delay: 7 },  { l: "81%", d: 13, delay: 11 },
  { l: "89%", d: 20, delay: 5 },  { l: "95%", d: 12, delay: 9 },
];

function Petals() {
  return (
    <div className="petals" aria-hidden="true">
      {PETALS.map((p, i) => (
        <span
          key={i}
          className="petal"
          style={{ left: p.l, animationDuration: `${p.d}s`, animationDelay: `${p.delay}s` }}
        />
      ))}
    </div>
  );
}

export default function Landing() {
  const [q, setQ] = useState<Qual | null>(null);
  const [rings, setRings] = useState<Rings | null>(null);
  const [sideb, setSideb] = useState<SideB | null>(null);
  const [active, setActive] = useState(0);
  const [playing, setPlaying] = useState(true);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    get<any>("/api/real/metrics")
      .then((d) => { setQ(d.qualification ?? null); setRings(d.rings ?? null); setSideb(d.side_b ?? null); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setTimeout(
      () => setActive((a) => (a + 1) % SCREENS.length), DWELL_MS
    );
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [active, playing]);

  // HashRouter reads its query string from inside the hash, so ?embed=1 goes after the route.
  const src = useMemo(
    () => `${location.pathname}${SCREENS[active].path}?embed=1`,
    [active]
  );

  return (
    <div className="lp">
      <header className="lp-nav">
        <div className="lp-wrap lp-nav-inner">
          <div className="lp-logo">AEGIS</div>
          <nav className="lp-nav-links">
            <a href="#problem">The problem</a>
            <a href="#evidence">Evidence</a>
            <a href="#numbers">Results</a>
            <a href="#honesty">Method</a>
          </nav>
          <Link to="/console" className="lp-btn lp-btn-primary">Open console</Link>
        </div>
      </header>

      {/* ---------------- hero ---------------- */}
      <section className="lp-hero">
        <Petals />
        <svg className="lp-branch" viewBox="0 0 340 200" aria-hidden="true">
          <path
            d="M340 8 C280 26 232 54 196 92 C176 114 160 134 140 148"
            stroke="#7d5560" strokeWidth="2.5" fill="none" strokeLinecap="round" opacity=".45"
          />
          <path d="M262 44 C250 58 240 64 224 68" stroke="#7d5560" strokeWidth="1.6" fill="none" strokeLinecap="round" opacity=".35" />
          <path d="M208 82 C202 98 196 106 182 114" stroke="#7d5560" strokeWidth="1.4" fill="none" strokeLinecap="round" opacity=".3" />
          {[[300, 22], [258, 46], [222, 70], [196, 92], [178, 112], [152, 138], [232, 62], [186, 106]].map(
            ([cx, cy], i) => (
              <g key={i} opacity={0.85 - i * 0.06}>
                {[0, 72, 144, 216, 288].map((a) => (
                  <ellipse
                    key={a} cx={cx} cy={cy} rx="5.5" ry="3.2"
                    fill={i % 3 === 0 ? "var(--petal-3)" : "var(--petal-2)"}
                    transform={`rotate(${a} ${cx} ${cy}) translate(4 0)`}
                  />
                ))}
                <circle cx={cx} cy={cy} r="1.8" fill="#f6c56b" />
              </g>
            )
          )}
        </svg>
        <div className="lp-wrap">
          <div className="lp-eyebrow">Adjudication Evidence &amp; Genuine-Intent Scoring</div>
          <h1 className="lp-h1">
            Most chargebacks are lost months before anyone fights them.
          </h1>
          <p className="lp-sub">
            Visa's Compelling Evidence 3.0 is the only thing that reliably wins a friendly-fraud
            dispute — and it needs data you had to capture 120 days ago. AEGIS tells you which
            disputes you can defend, which submitted receipts are fabricated, and what to start
            recording today so next quarter's cases qualify.
          </p>
          <div className="lp-cta-row">
            <Link to="/console" className="lp-btn lp-btn-primary lp-btn-lg">Open the console</Link>
            <Link to="/console/real" className="lp-btn lp-btn-ghost lp-btn-lg">See the real-data results</Link>
          </div>
          <div className="lp-trust">
            Measured on IEEE-CIS · CORD · SROIE — public datasets, not simulations
          </div>
        </div>

        {/* ---------------- live product frame ---------------- */}
        <div className="lp-wrap">
          <div className="lp-demo">
            <div className="lp-demo-chrome">
              <span className="lp-dot" /><span className="lp-dot" /><span className="lp-dot" />
              <div className="lp-urlbar">
                <span className="lp-lock">●</span>
                aegis.run.app/{SCREENS[active].path.replace("#/", "")}
              </div>
              <button
                className="lp-playbtn"
                onClick={() => setPlaying((p) => !p)}
                aria-label={playing ? "Pause tour" : "Play tour"}
              >
                {playing ? "❙❙" : "▶"}
              </button>
            </div>
            {/* Genuinely the running application, not a recording. */}
            <iframe
              key={src}
              className="lp-demo-frame"
              src={src}
              title="AEGIS console, running live"
              loading="lazy"
            />
            <div className="lp-demo-tabs">
              {SCREENS.map((s, i) => (
                <button
                  key={s.path}
                  className={i === active ? "on" : ""}
                  onClick={() => { setActive(i); setPlaying(false); }}
                >
                  {s.label}
                  {i === active && playing && <span className="lp-progress" key={`p${active}`} />}
                </button>
              ))}
            </div>
          </div>
          <div className="lp-demo-caption">
            <strong>{SCREENS[active].label}.</strong> {SCREENS[active].blurb}
            <span className="lp-live"> ● live — this is the running app, not a video</span>
          </div>
        </div>
      </section>

      {/* ---------------- the problem ---------------- */}
      <section className="lp-band" id="problem">
        <div className="lp-wrap">
          <h2 className="lp-h2">Two things are true, and nobody has connected them.</h2>
          <div className="lp-two">
            <article className="lp-story">
              <div className="lp-num">01</div>
              <h3>The only rule that wins is decided before the fight.</h3>
              <p>
                CE 3.0 needs two prior undisputed transactions on the same credential, aged
                120–364 days, matching the disputed order on specific data elements. Most
                merchants discover at dispute time that their pipeline never stored a device
                fingerprint. By then it is four months too late to fix.
              </p>
            </article>
            <article className="lp-story">
              <div className="lp-num">02</div>
              <h3>The evidence layer itself is now under attack.</h3>
              <p>
                The share of flagged fake receipts that are AI-generated went from 0% in March
                2025 to 70.8% by mid-May 2026. When a customer submits a “damaged item” photo or
                a fabricated receipt, no merchant dispute tool checks whether it is real.
              </p>
            </article>
          </div>
          <div className="lp-quote">
            <p>
              A <strong>recycled receipt</strong> — genuine, unaltered, from a different real
              order — is forensically perfect. No pixel-level method will ever flag it. AEGIS
              catches it because it holds the transaction ledger and can check the amount, date
              and merchant against the actual charge.
            </p>
            <footer>Owning both sides is what makes the detection possible.</footer>
          </div>
        </div>
      </section>

      {/* ---------------- numbers ---------------- */}
      <section className="lp-band lp-band-tint" id="numbers">
        <div className="lp-wrap">
          <h2 className="lp-h2">We ran Visa's actual gate over real chargebacks.</h2>
          <p className="lp-lede">
            Not a simulation. 590,540 real card-not-present transactions from IEEE-CIS, whose
            fraud label the data provider defines as a reported chargeback.
          </p>

          {q ? (
            <div className="lp-funnel">
              {[
                { n: q.n_chargebacks.toLocaleString("en-US"), l: "real chargebacks" },
                { n: q.n_assessable.toLocaleString("en-US"), l: "assessable" },
                { n: String(q.funnel.cleared_prior_gate), l: "clear the history gate" },
                { n: String(q.funnel.qualified), l: "actually qualify", hot: true },
              ].map((s, i) => (
                <div className="lp-funnel-step" key={i}>
                  <div className={`lp-funnel-n ${s.hot ? "hot" : ""}`}>{s.n}</div>
                  <div className="lp-funnel-l">{s.l}</div>
                  {i < 3 && <div className="lp-funnel-arrow">→</div>}
                </div>
              ))}
            </div>
          ) : (
            <div className="lp-funnel-skeleton">loading live results…</div>
          )}

          {q && (
            <div className="lp-callout">
              <strong>
                {q.funnel.blocked_no_main_anchor} of the {q.funnel.cleared_prior_gate} cases that
                clear the history gate fail for one reason: no IP or device was ever captured.
              </strong>{" "}
              A tool reading the rule as “any two of four” — a common misreading — tells the
              merchant to fight all {q.naive_false_positives} of them. Every one would be filed
              and lost.
            </div>
          )}

          <div className="lp-three">
            <div className="lp-stat">
              <div className="lp-stat-n">{sideb ? `${(sideb.held_out.precision * 100).toFixed(0)}%` : "—"}</div>
              <div className="lp-stat-l">forensic precision on real receipt photographs</div>
            </div>
            <div className="lp-stat">
              <div className="lp-stat-n">{rings ? `${rings.lift.toFixed(1)}×` : "—"}</div>
              <div className="lp-stat-l">chargeback lift inside device-linked abuse rings</div>
            </div>
            <div className="lp-stat">
              <div className="lp-stat-n">21</div>
              <div className="lp-stat-l">cited rulebook passages behind every answer</div>
            </div>
          </div>
        </div>
      </section>

      {/* ---------------- the two sides ---------------- */}
      <section className="lp-band" id="evidence">
        <div className="lp-wrap">
          <h2 className="lp-h2">Both directions of the evidence war.</h2>
          <div className="lp-two">
            <article className="lp-side">
              <span className="lp-side-badge">Side A</span>
              <h3>Can we defend this?</h3>
              <p>
                The CE 3.0 criteria as a versioned rulebook, not a checkbox. Every dispute gets a
                qualification verdict, the exact blocking gap, and the single field that would
                have flipped it — plus a portfolio view of what to start capturing now.
              </p>
              <ul>
                <li>The real tiered Main/Secondary match rule</li>
                <li>Gap diagnosis with single-field counterfactuals</li>
                <li>Calibrated win probability and per-case break-even</li>
              </ul>
            </article>
            <article className="lp-side">
              <span className="lp-side-badge alt">Side B</span>
              <h3>Is their evidence real?</h3>
              <p>
                Arithmetic integrity, provenance, compression forensics, typography, sensor
                noise — and a cross-check against the transaction record that no
                document-forensics tool can perform, because it has no ledger.
              </p>
              <ul>
                <li>Deterministic tamper rules plus a calibrated model</li>
                <li>Verdicts conservative by design — accusing a customer is costly</li>
                <li>Hash-chained log shaped for Section 63 BSA, 2023</li>
              </ul>
            </article>
          </div>
        </div>
      </section>

      {/* ---------------- method / honesty ---------------- */}
      <section className="lp-band lp-band-dark" id="honesty">
        <div className="lp-wrap">
          <h2 className="lp-h2 lp-h2-inv">We publish the results that disappoint us.</h2>
          <p className="lp-lede lp-lede-inv">
            A model that only reports its wins is a marketing artefact. These are in the console,
            on the Metrics page, permanently.
          </p>
          <div className="lp-three lp-three-inv">
            <div className="lp-honest">
              <h4>Forensics is worse on real photos</h4>
              <p>
                {sideb ? `${(sideb.held_out.recall * 100).toFixed(1)}%` : "49.4%"} recall on real
                receipts, below the 77.0% human baseline — against 97.3% on our own rendered
                fakes. Real manipulations carry no arithmetic tell.
              </p>
            </div>
            <div className="lp-honest">
              <h4>We found our own leak</h4>
              <p>
                A prior-chargeback feature lifted PR-AUC to 0.847. Vesta propagates fraud labels
                across an account, so it restated the answer. Removed; the honest number is 0.475.
              </p>
            </div>
            <div className="lp-honest">
              <h4>Time costs accuracy</h4>
              <p>
                A random split reports 0.703. The same model scores 0.475 predicting forward in
                time. We report the second, because that is how it would be deployed.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ---------------- final CTA ---------------- */}
      <section className="lp-final">
        <div className="lp-wrap">
          <h2 className="lp-h2">See which of your disputes are actually defensible.</h2>
          <p className="lp-lede">
            The console runs on public data out of the box. Connect Stripe with a read-only key
            to check its native CE 3.0 verdict against the rulebook on your own disputes.
          </p>
          <div className="lp-cta-row">
            <Link to="/console" className="lp-btn lp-btn-primary lp-btn-lg">Open the console</Link>
            <Link to="/console/connections" className="lp-btn lp-btn-ghost lp-btn-lg">Connect Stripe</Link>
          </div>
        </div>
      </section>

      <footer className="lp-footer">
        <div className="lp-wrap">
          <div className="lp-footer-top">
            <div className="lp-logo">AEGIS</div>
            <div className="lp-footer-links">
              <Link to="/console">Console</Link>
              <Link to="/console/metrics">Metrics</Link>
              <Link to="/console/rules">Rulebook</Link>
              <Link to="/console/assistant">Assistant</Link>
            </div>
          </div>
          <div className="lp-disclaimer">
            <p>
              <strong>Defence-only.</strong> AEGIS acts only after a transaction, dispute or return
              exists. No component predicts how to commit, evade or fabricate fraud. The forensic
              model detects tampering and cannot generate it. The packet builder never
              auto-submits — it assembles evidence for a human to review and file.
            </p>
            <p>
              <strong>Data.</strong> Transaction results are computed on IEEE-CIS Fraud Detection
              (Vesta Corporation). Evidence results use real receipt photographs from CORD
              (Naver Clova) and SROIE (ICDAR 2019 Robust Reading Challenge), with manipulations
              applied programmatically following the methodology of DocTamper (CVPR 2023) and
              AIForge-Doc (2026). Amounts are USD in the source and shown in ₹ at a stated rate.
            </p>
            <p>
              <strong>What remains modelled.</strong> No public dataset carries a “won the
              representment” outcome, so the win-probability model and the Cost Lab that consumes
              it are driven by a documented structural model and labelled as such throughout.
              Card-network rules are encoded as of April 2026 and change; the rulebook is
              versioned with effective dates. Not legal advice.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
