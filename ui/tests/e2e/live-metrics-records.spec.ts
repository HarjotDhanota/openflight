import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { Shot, ShotMetric } from '../../src/types/shot';
import { KIOSK_VIEWPORTS, overflowingLiveMetricMetadata, overflowingLiveMetricValues } from './helpers';

for (const viewport of KIOSK_VIEWPORTS) {
  test(`Live uses published records at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await page.getByRole('button', { name: 'Close Select club' }).click();
    await page.getByRole('button', { name: 'Simulate shot', exact: true }).evaluate((button) => {
      button.style.visibility = 'hidden';
    });
    await page.evaluate(async () => {
      const { useShotStore } = await import('/src/stores/useShotStore.ts');
      const { useProfileStore } = await import('/src/stores/useProfileStore.ts');
      const record = (
        value: number | null,
        unit: string,
        status: ShotMetric['status'] = 'measured',
        reason: string | null = null
      ): ShotMetric => ({
        value,
        unit,
        status,
        reason,
        source: 'known-answer fixture',
        range: null,
        frame: null,
        note: null,
        normalized: null,
      });
      const shot = {
        timestamp: '2026-10-25T12:00:00',
        club: 'driver',
        profile_id: useProfileStore.getState().activeProfileId,
        ball_speed_mph: 96,
        club_speed_mph: 74,
        smash_factor: 1.3,
        estimated_carry_yards: 160,
        carry_range: [150, 170],
        launch_angle_vertical: 40,
        launch_angle_horizontal: 70.6,
        launch_angle_confidence: 0.99,
        spin_rpm: 6482,
        spin_quality: 'high',
        spin_source: 'measured',
        spin_axis_deg: 4,
        club_path_deg: 20,
        club_angle_deg: -5,
        experimental_face_angle_deg: 30,
        metrics: {
          ball_speed: record(96, 'mph'),
          club_speed: record(74, 'mph'),
          smash_factor: record(1.3, '', 'calculated'),
          carry: record(null, 'yd', 'not_measured', 'spin not measured on this shot'),
          launch_vertical: record(14.2, 'deg', 'experimental'),
          launch_horizontal: record(null, 'deg', 'not_measured', 'horizontal launch withheld by producer'),
          spin_rate: record(6482, 'rpm', 'experimental'),
          spin_axis: record(4, 'deg', 'experimental'),
          club_path: record(-2, 'deg', 'estimated'),
          attack_angle: record(-5, 'deg', 'experimental'),
          face_angle: record(1.5, 'deg', 'experimental'),
        },
      } as Shot;
      useShotStore.getState().setShots([shot]);
    });
    const cards = page.locator('.live-panel__grid .metric-card');
    const tile = (label: string) => cards.filter({ has: page.locator('.metric-card__label', { hasText: label }) });
    await expect(cards).toHaveCount(10);
    await expect(tile('V. launch').locator('.metric-card__value')).toHaveText('14.2');
    await expect(tile('V. launch').locator('[data-label="experimental"]')).toBeVisible();
    await expect(tile('V. launch').locator('.metric-card__confidence')).toHaveCount(0);
    await expect(tile('H. launch').locator('.metric-card__value')).toHaveText('—');
    await expect(tile('H. launch').locator('.metric-card__reason')).toHaveText(
      'horizontal launch withheld by producer'
    );
    await expect(tile('Carry').locator('.metric-card__value')).toHaveText('—');
    await expect(tile('Club path').locator('.metric-card__value')).toHaveText('-2.0');
    await expect(tile('Club path').locator('.metric-card__subtext')).toContainText('experimental');
    expect(await overflowingLiveMetricValues(page)).toEqual([]);
    expect(await overflowingLiveMetricMetadata(page)).toEqual([]);
    if (process.env.OPENFLIGHT_SCREENSHOT_DIR) {
      mkdirSync(process.env.OPENFLIGHT_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({
        path: resolve(process.env.OPENFLIGHT_SCREENSHOT_DIR, `live-${viewport.width}x${viewport.height}.png`),
      });
    }
  });
}
