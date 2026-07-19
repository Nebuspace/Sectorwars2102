import { test as authTest, expect } from '../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../utils/auth.utils';

// WO-ADM-EMERG-KERNEL: PlayerDetailEditor's four emergency buttons used to
// live-fire POST /api/v1/admin/players/{id}/emergency, a route that does
// not exist (the only emergency route is ship-scoped, admin_ships.py:205).
// The buttons are now honest-disabled until WO-ADM-EMERG-BACKEND ships a
// real endpoint (Max-gated). This spec proves the disable is real (native
// `disabled`, not just `loading`-derived) and that the missing-endpoint
// path is named in each button's title.
//
// NOTE: at the time this spec was authored, the deployed dev host still
// runs the pre-fix build. This spec is expected to go green on the next
// deploy, not necessarily today.
const EMERGENCY_PATH = '/api/v1/admin/players/{id}/emergency';

authTest.describe('Admin UI - PlayerDetailEditor emergency buttons (WO-ADM-EMERG-KERNEL)', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await page.goto('/players', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('h1.page-title')).toHaveText('Players');
    await page.waitForSelector('table.table tbody tr', { timeout: 15000 });
  });

  authTest('all four emergency buttons are honest-disabled, name the missing endpoint, and never call it', async ({ page }) => {
    // Any request touching the emergency path — from any origin, any
    // method — would prove a live-fire; there should be none.
    const emergencyRequests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/emergency')) {
        emergencyRequests.push(`${request.method()} ${request.url()}`);
      }
    });

    await page.locator('table.table tbody tr').first().locator('button[title="Edit Player"]').click();

    const editor = page.locator('.player-detail-editor');
    await expect(editor).toBeVisible();

    const emergencyButtons = editor.locator('.emergency-btn');
    await expect(emergencyButtons).toHaveCount(4);

    for (let i = 0; i < 4; i++) {
      const button = emergencyButtons.nth(i);
      await expect(button).toBeDisabled();
      await expect(button).toHaveAttribute('title', new RegExp(EMERGENCY_PATH.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      // A native `disabled` button does not dispatch click handlers even
      // when force-clicked — this is the live-fire check, not a no-op.
      await button.click({ force: true });
    }

    expect(emergencyRequests).toEqual([]);
  });

  authTest('editing a field and saving still fires the real player PATCH (regression leg)', async ({ page }) => {
    await page.locator('table.table tbody tr').first().locator('button[title="Edit Player"]').click();

    const editor = page.locator('.player-detail-editor');
    await expect(editor).toBeVisible();

    const requestPromise = page.waitForRequest(
      (request) => /\/api\/v1\/admin\/players\/[^/]+$/.test(request.url()) && request.method() === 'PATCH'
    );

    const usernameInput = editor.locator('.form-group input[type="text"]').first();
    const currentValue = await usernameInput.inputValue();
    await usernameInput.fill(`${currentValue}-e2e`);

    await editor.locator('button:has-text("Save Changes")').click();

    const request = await requestPromise;
    expect(request.method()).toBe('PATCH');
  });
});
