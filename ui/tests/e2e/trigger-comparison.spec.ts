import { expect, test } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

for (const viewport of KIOSK_VIEWPORTS) {
  test(`unpublished trigger evidence is reviewable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route('**/api/tester/analysis**', (route) =>
      route.fulfill({
        json: {
          state: 'complete',
          review_ready: true,
          job: { finished_at: 'saved', errors: [] },
          bundles: [],
        },
      })
    );
    await page.route('**/api/tester/session-review?**', (route) =>
      route.fulfill({
        json: {
          tester_id: 'pilot-1',
          attempts: [],
          runs: [
            {
              arm_id: 'arm5',
              run: 'run-01',
              errors: [],
              rejected_triggers: [],
              trigger_comparison: {
                settings: { label: 'Hybrid confirmation (OPS retention)' },
                counts: { attempts_including_no_shot: 1, unlabelled_attempts: 1, missed_labelled_real_shots: 0 },
                limitations: ['Unlabelled activity is unknown. Counterfactual decisions cannot recover lost buffers.'],
                attempts: [
                  {
                    attempt: 'native-sync/owned/attempt-001',
                    published: false,
                    decision: { reason: 'hybrid_unconfirmed: IWR outside actual saved coverage' },
                    candidates: [
                      {
                        detector: 'ops',
                        event_identity: 'own',
                        decision: 'ignored',
                        reason: 'capture_already_requested',
                      },
                    ],
                    association: { physical_timing_qualified: false, camera_coverage_mono_interval_ns: [10, 20] },
                    camera_clips: [],
                    counterfactual_policy_decisions: { independent: { publication_allowed: true } },
                  },
                ],
              },
            },
          ],
        },
      })
    );
    await page.goto('/session-review.html?tester_id=pilot-1');
    const comparison = page.locator('.trigger-comparison');
    await comparison.locator('> summary').click();
    await expect(comparison).toContainText('no published shot');
    await expect(comparison).toContainText('hybrid_unconfirmed: IWR outside actual saved coverage');
    await comparison.getByText('Candidate timeline (host observations)', { exact: true }).click();
    await expect(comparison).toContainText('capture_already_requested');
    await comparison.getByText('Actual capture coverage and association', { exact: true }).click();
    await expect(comparison).toContainText('physical_timing_qualified');
    await expect(comparison).toContainText('Counterfactual decisions cannot recover lost buffers.');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: test.info().outputPath('unpublished-trigger-review.png'), fullPage: true });
  });
}
