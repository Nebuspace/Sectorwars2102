import { test, expect } from '@playwright/test';
import { installSmokeAuth } from '../helpers/smoke-auth';

/**
 * WO-ADM-WS-RECONNECT-PROGRESS — reconnecting chip shows attempt N/max.
 * Uses __ADMIN_WS_FORCE_RECONNECTING__ (does not trip the #166 gave-up banner).
 */
test.describe('Admin UI WS reconnect progress chip', () => {
  test('shows attempt progress while reconnecting', async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as {
        __ADMIN_WS_FORCE_RECONNECTING__: { attempt: number; max: number };
      }).__ADMIN_WS_FORCE_RECONNECTING__ = { attempt: 2, max: 5 };
    });
    await installSmokeAuth(page);

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

    const chip = page.getByTestId('ws-connection-chip');
    await expect(chip).toBeVisible({ timeout: 15_000 });
    await expect(chip).toContainText('Reconnecting… (2/5)');
    await expect(page.getByTestId('ws-gave-up-banner')).toHaveCount(0);
  });
});
