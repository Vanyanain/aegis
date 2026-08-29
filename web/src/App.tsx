import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import Landing from "./pages/Landing";
import Overview from "./pages/Overview";
import RealData from "./pages/RealData";
import Disputes from "./pages/Disputes";
import Case from "./pages/Case";
import CostLab from "./pages/CostLab";
import Metrics from "./pages/Metrics";
import Rules from "./pages/Rules";
import Assistant from "./pages/Assistant";
import Integrations from "./pages/Integrations";

// The marketing page owns "/" and the application lives under "/console", the same split
// Mercury uses between mercury.com and the app. Keeping both at the root would force the
// landing page to render inside the console shell, sidebar and all.
const NAV = [
  ["/console", "Overview"],
  ["/console/real", "Real data"],
  ["/console/disputes", "Disputes"],
  ["/console/costlab", "Cost Lab"],
  ["/console/metrics", "Metrics"],
  ["/console/rules", "Rulebook"],
  ["/console/assistant", "Assistant"],
  ["/console/connections", "Connections"],
] as const;

function Console({ embedded }: { embedded: boolean }) {
  return (
    <div className="shell" style={embedded ? { gridTemplateColumns: "1fr" } : undefined}>
      {!embedded && <aside className="sidebar">
        <div className="brand">
          <h1>AEGIS</h1>
          <div className="tag">
            Adjudication Evidence
            <br />&amp; Genuine-Intent Scoring
          </div>
        </div>
        <nav className="nav">
          {NAV.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/console"}
              className={({ isActive }) => (isActive ? "on" : "")}
            >
              <span className="dot" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidefoot">
          <NavLink to="/" style={{ color: "var(--indigo)" }}>← Back to site</NavLink>
          <br />
          DEFENCE-ONLY · no auto-submit
          <br />
          IEEE-CIS · CORD · SROIE
        </div>
      </aside>}
      <main className="main">
        <Routes>
          <Route index element={<Overview />} />
          <Route path="real" element={<RealData />} />
          <Route path="disputes" element={<Disputes />} />
          <Route path="disputes/:id" element={<Case />} />
          <Route path="costlab" element={<CostLab />} />
          <Route path="metrics" element={<Metrics />} />
          <Route path="rules" element={<Rules />} />
          <Route path="assistant" element={<Assistant />} />
          <Route path="connections" element={<Integrations />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  // The landing page embeds the console in an iframe for its live tour. Inside that frame the
  // sidebar would be redundant chrome, so it is suppressed via ?embed=1.
  // The landing page tours the console inside an iframe. A sidebar nested inside that frame
  // is redundant chrome that makes the demo look like a screenshot of a screenshot, so
  // ?embed=1 drops it and lets the content use the full width.
  const embedded = new URLSearchParams(useLocation().search).has("embed");
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/console/*" element={<Console embedded={embedded} />} />
    </Routes>
  );
}
