import { NavLink, Route, Routes } from "react-router-dom";
import Overview from "./pages/Overview";
import Disputes from "./pages/Disputes";
import Case from "./pages/Case";
import CostLab from "./pages/CostLab";
import Metrics from "./pages/Metrics";
import Rules from "./pages/Rules";

const NAV = [
  ["/", "Overview"],
  ["/disputes", "Disputes"],
  ["/costlab", "Cost Lab"],
  ["/metrics", "Metrics"],
  ["/rules", "Rulebook"],
] as const;

export default function App() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <h1>AEGIS</h1>
          <div className="tag">
            Adjudication Evidence
            <br />&amp; Genuine-Intent Scoring
          </div>
        </div>
        <nav className="nav">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "on" : "")}>
              <span className="dot" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidefoot">
          DEFENCE-ONLY
          <br />
          No auto-submit
          <br />
          Synthetic data
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/disputes" element={<Disputes />} />
          <Route path="/disputes/:id" element={<Case />} />
          <Route path="/costlab" element={<CostLab />} />
          <Route path="/metrics" element={<Metrics />} />
          <Route path="/rules" element={<Rules />} />
        </Routes>
      </main>
    </div>
  );
}
