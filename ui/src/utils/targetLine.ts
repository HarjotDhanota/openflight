/**
 * The kiosk's target line (GET /api/target-line): the zero every left/right angle is
 * measured from. Without it, left and right are measured from where the unit points.
 */
export interface TargetLine {
  status: 'set' | 'not_set';
  /** + right of where the unit points. */
  yawDeg: number | null;
  /** The server's total uncertainty (parallax and touch scale); the only ± shown. */
  uncertaintyDeg: number | null;
}

const finite = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);

export function parseTargetLine(body: unknown): TargetLine | null {
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  if (record.status !== 'set' && record.status !== 'not_set') return null;
  return {
    status: record.status,
    yawDeg: finite(record.yaw_deg),
    uncertaintyDeg: finite(record.total_uncertainty_deg),
  };
}

export function targetLineYawText(yawDeg: number): string {
  const rounded = Math.round(yawDeg * 10) / 10;
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(1)}°`;
}
