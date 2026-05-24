import type { MeasurementRow } from "@/lib/types";

interface Props { rows: MeasurementRow[] }

// Parse a measurement value into a finite number when possible. Accepts
// numerics and "6 h 42"-style strings (hours + minutes → minutes).
function toNumber(v: number | string): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const hm = v.match(/^\s*(\d+)\s*h\s*(\d{1,2})\s*$/i);
  if (hm) return parseInt(hm[1], 10) * 60 + parseInt(hm[2], 10);
  const n = parseFloat(v.replace(",", ".").replace(/[^\d.\-]/g, ""));
  return Number.isFinite(n) ? n : null;
}

function fmtDelta(d: number, unitHint: string): string {
  const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
  const abs = Math.abs(d);
  // Keep ≤ 1 decimal except for integer-domain deltas (minutes etc.).
  const isInt = unitHint.includes("min") || Math.abs(d - Math.round(d)) < 0.05;
  const body = isInt ? Math.round(abs).toString() : abs.toFixed(1).replace(".", ",");
  return `${sign}${body}`;
}

export function MeasurementsTable({ rows }: Props) {
  if (!rows.length) return null;
  // Rows are sorted chronologically descending (most recent first), so the
  // "previous" measurement is the next row. Skip rows where parsing fails.
  const enriched = rows.map((r, i) => {
    if (r.delta) return { row: r, delta: r.delta };
    const cur = toNumber(r.value);
    const prev = rows[i + 1] ? toNumber(rows[i + 1].value) : null;
    if (cur === null || prev === null) return { row: r, delta: "" };
    const d = cur - prev;
    if (Math.abs(d) < 1e-9) return { row: r, delta: "±0" };
    return { row: r, delta: fmtDelta(d, r.unit ?? "") };
  });

  return (
    <section className="measurements" aria-labelledby="measurements-heading">
      <h2 id="measurements-heading" className="section-label">Dernières mesures</h2>
      <table>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col" className="num">Valeur</th>
            <th scope="col" className="num">Δ</th>
          </tr>
        </thead>
        <tbody>
          {enriched.map(({ row: r, delta }, i) => (
            <tr key={i}>
              <td>{r.date}</td>
              <td className="num">{typeof r.value === "number" ? r.value.toString().replace(".", ",") : r.value} <span className="unit">{r.unit}</span></td>
              <td className={"num delta" + (r.off ? " off" : "")}>{delta}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
