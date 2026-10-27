import { expect, test } from '@playwright/test';
import { KIOSK_VIEWPORTS } from './helpers';

test.use({ hasTouch: true });

for (const viewport of KIOSK_VIEWPORTS) {
  test.describe(`tester keyboard at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true });
    test('enters and edits an ID entirely by touch without leaving the tester', async ({ page }) => {
      await page.goto('/tester.html?screen=unit');
      await page.getByLabel('Your tester ID').tap();
      const dialog = page.getByRole('dialog', { name: 'Enter tester ID' });
      await expect(dialog).toBeVisible();
      const key = (name: string) => dialog.getByRole('button', { name, exact: true });
      for (const char of ['t', 'e', 's', 't', '-', '1', '.', '_']) await key(char).tap();
      const input = page.getByLabel('Your tester ID');
      await expect(input).toHaveValue('test-1._');
      await key('Backspace').tap();
      await key('Shift').tap();
      await key('A').tap();
      await expect(input).toHaveValue('test-1.A');
      for (const target of [input, key('Use this ID'), ...(await dialog.locator('#tester-touch-keys button').all())]) {
        await expect(target).toBeInViewport();
        const box = (await target.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      }
      await page.screenshot({
        path: test.info().outputPath(`tester-keyboard-${viewport.width}x${viewport.height}.png`),
      });
      await key('Use this ID').tap();
      await expect(dialog).not.toBeVisible();
      await expect(page.locator('#tester-current-id')).toHaveText('test-1.A');
      await expect(page).toHaveURL(/tester.html\?screen=unit$/);
      await page.getByRole('button', { name: 'Change tester', exact: true }).tap();
      await input.tap();
      await expect(dialog).toBeVisible();
      await key('Hide keyboard').tap();
      await expect(dialog).not.toBeVisible();
      await input.fill('physical-2');
      await input.press('Enter');
      await expect(page.locator('#tester-current-id')).toHaveText('physical-2');
    });
  });
}

test('phone keeps its native text entry', async ({ page }) => {
  await page.goto('/tester.html');
  await page.getByLabel('Your tester ID').tap();
  await expect(page.getByRole('dialog', { name: 'Enter tester ID' })).not.toBeVisible();
  await expect(page.getByLabel('Your tester ID')).not.toHaveAttribute('inputmode', 'none');
});

test('keyboard respects the cursor, ID length, validation and Escape', async ({ page }) => {
  await page.goto('/tester.html?screen=unit');
  const input = page.getByLabel('Your tester ID');
  await input.tap();
  const dialog = page.getByRole('dialog', { name: 'Enter tester ID' });
  const key = (name: string) => dialog.getByRole('button', { name, exact: true });
  await input.fill('abcd');
  await input.evaluate((node: HTMLInputElement) => node.setSelectionRange(1, 3));
  await key('x').tap();
  await expect(input).toHaveValue('axd');
  await key('Backspace').tap();
  await expect(input).toHaveValue('ad');
  await input.fill('a'.repeat(80));
  await key('z').tap();
  await expect(input).toHaveValue('a'.repeat(80));
  await input.fill('-bad');
  await key('Use this ID').tap();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(input).toHaveValue('-bad');
});
