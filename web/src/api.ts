// Thin fetch layer. Same-origin in production (FastAPI serves this bundle), proxied in dev.

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return r.json();
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`);
  return r.json();
}

// Rupee formatting used everywhere. Indian grouping (lakh/crore) is what the audience reads.
export const inr = (v: number, frac = 0) =>
  "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: frac, minimumFractionDigits: frac });

export const inrCompact = (v: number) => {
  const a = Math.abs(v);
  if (a >= 1e7) return "₹" + (v / 1e7).toFixed(2) + " Cr";
  if (a >= 1e5) return "₹" + (v / 1e5).toFixed(2) + " L";
  if (a >= 1e3) return "₹" + (v / 1e3).toFixed(1) + "k";
  return "₹" + v.toFixed(0);
};

export const pct = (v: number, frac = 1) => (v * 100).toFixed(frac) + "%";

// Counts use Western grouping. Indian lakh grouping is correct for RUPEES, but applied to a
// row count it renders 590,540 as "5,90,540", which reads as a different number entirely to
// most people looking at a dataset size.
export const count = (v: number) => v.toLocaleString("en-US");
