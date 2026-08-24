import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CriteriaTable } from './CriteriaTable';

describe('criteria landing table', () => {
  it('makes availability and tail error co-equal and labels hardware policy', () => {
    const html = renderToStaticMarkup(<CriteriaTable rows={[
      {
        club: 'poc_driver', candidate: 'ambient_500us', role: 'primary', n: 200,
        solve_rate: 1, median_mm: 0.93, p90_mm: 1.82,
        signed_horizontal_median_mm: 0, signed_vertical_median_mm: 0,
        iou_median: 0.9, rejections: {}, passes: true,
      },
      {
        club: 'poc_driver', candidate: 'strobed_10us', role: 'comparison_only', n: 200,
        solve_rate: 1, median_mm: 0.95, p90_mm: 1.86,
        signed_horizontal_median_mm: 0, signed_vertical_median_mm: 0,
        iou_median: 0.9, rejections: {}, passes: true,
      },
    ]} />);

    expect(html).toContain('Availability');
    expect(html).toContain('p90');
    expect(html).toContain('PRIMARY — PHASE A');
    expect(html).toContain('COMPARISON ONLY');
  });
});
