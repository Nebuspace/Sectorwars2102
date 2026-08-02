import { test, expect } from '@playwright/test';
import { installSmokeAuth } from '../helpers/smoke-auth';

/**
 * WO-ADM-WS-GAVEUP-BANNER — abandoned reconnect must be visible in the shell
 * (not only console.log). Uses the __ADMIN_WS_FORCE_GAVE_UP__ test hook.
 */
test.describe('Admin UI WS gave-up banner', () => {
  test('shows banner with Retry when reconnect is abandoned', async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __ADMIN_WS_FORCE_GAVE_UP__: boolean }).__ADMIN_WS_FORCE_GAVE_UP__ = true;
    });
    await installSmokeAuth(page);

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

    const banner = page.getByTestId('ws-gave-up-banner');
    await expect(banner).toBeVisible({ timeout: 15_000 });
    await expect(banner).toContainText('Live updates disconnected');
    await expect(page.getByTestId('ws-gave-up-retry')).toBeVisible();
    await expect(page.getByTestId('ws-gave-up-dismiss')).toBeVisible();
  });
});
