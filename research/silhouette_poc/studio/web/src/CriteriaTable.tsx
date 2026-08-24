import { criteriaRoleLabel, label, mm, percent, rejectionTotal } from './model';
import type { CriteriaRow } from './types';

export function CriteriaTable({ rows }: { rows: CriteriaRow[] }) {
  return (
    <div className="table-wrap">
      <table className="criteria-table">
        <thead>
          <tr>
            <th>Hardware arm</th><th>Club</th><th>N</th>
            <th className="availability">Availability<br /><span>Solve / rejected</span></th>
            <th>Median</th><th>p90</th><th>Signed H med / p90</th><th>Signed V med / p90</th><th>IoU</th><th>Gate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.club}-${row.candidate}`} className={row.role}>
              <td>
                <strong>{label(row.candidate)}</strong>
                <span className={`policy ${row.role}`}>{criteriaRoleLabel(row.role)}</span>
              </td>
              <td>{label(row.club)}</td><td>{row.n}</td>
              <td className="availability"><strong>{percent(row.solve_rate)}</strong><span>{rejectionTotal(row.rejections)} rejected</span></td>
              <td>{mm(row.median_mm)}</td><td>{mm(row.p90_mm)}</td>
              <td>{mm(row.signed_horizontal_median_mm)} / {mm(row.signed_horizontal_p90_mm ?? null)}</td>
              <td>{mm(row.signed_vertical_median_mm)} / {mm(row.signed_vertical_p90_mm ?? null)}</td>
              <td>{row.iou_median == null ? '—' : row.iou_median.toFixed(3)}</td>
              <td><span className={`gate ${row.passes ? 'pass' : 'fail'}`}>{row.passes ? 'PASS' : 'FAIL'}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
