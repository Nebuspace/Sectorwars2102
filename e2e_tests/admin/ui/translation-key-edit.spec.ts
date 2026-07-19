import { v4 as uuidv4 } from 'uuid';
import { test as authTest, expect } from '../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../utils/auth.utils';

/**
 * WO-NEON-NH6-ADMIN-MUTATIONS — Translation Management safe-kernel mutation.
 *
 * Covers the single-key edit/add flow against the already-registered
 * POST /i18n/admin/translation/{lang}/{namespace} route, gated by the shared
 * in-shell confirm dialog (.confirm-dialog / useConfirm). Bulk-overwrite and
 * initialize are intentionally NOT wired here — that destructive surface
 * stays gated pending Max's ruling (see DECISIONS) — so one test asserts
 * their absence.
 */

const BASE_URL = 'http://localhost:3001';

authTest.describe('Admin UI - Translation key edit', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await page.goto(`${BASE_URL}/translations`);
    await page.waitForSelector('.translation-management', { timeout: 15000 });
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  authTest('renders zero destructive controls (no bulk-import/initialize)', async ({ page }) => {
    const destructiveControls = page.locator('button, a').filter({ hasText: /bulk|initialize/i });
    expect(await destructiveControls.count()).toBe(0);
  });

  authTest(
    'editing/adding a translation key issues POST and the value re-renders',
    async ({ page }) => {
      const langRow = page.locator('.tm-table tbody tr').first();
      await expect(langRow).toBeVisible({ timeout: 10000 });

      await langRow.locator('button:has-text("View progress")').click();

      // Scope to the Progress section specifically — the page renders a
      // second, distinct .tm-table for the language overview above it.
      const progressSection = page
        .locator('section.tm-section')
        .filter({ has: page.locator('h3.tm-section-title', { hasText: /^Progress:/ }) });
      await expect(progressSection).toBeVisible({ timeout: 10000 });

      const namespaceRow = progressSection.locator('table.tm-table tbody tr').first();
      await expect(namespaceRow).toBeVisible({ timeout: 10000 });

      await namespaceRow.locator('button:has-text("Browse")').click();

      const keysPanel = progressSection.locator('.tm-namespace-keys');
      await expect(keysPanel).toBeVisible({ timeout: 10000 });

      const uniqueSuffix = uuidv4().substring(0, 8);
      const value = `E2E value ${uniqueSuffix}`;
      const existingKeyRow = keysPanel.locator('table.tm-keys-table tbody tr').first();
      const hasExistingKey = (await existingKeyRow.count()) > 0;

      if (hasExistingKey) {
        await existingKeyRow.locator('button:has-text("Edit")').click();
      } else {
        await keysPanel.locator('button:has-text("+ Add Key")').click();
      }

      await expect(page.locator('.modal')).toBeVisible({ timeout: 5000 });
      const modal = page.locator('.modal');

      if (!hasExistingKey) {
        await modal.locator('input[placeholder="buttons.save"]').fill(`e2e.test.${uniqueSuffix}`);
      }
      await modal.locator('textarea').fill(value);

      const responsePromise = page.waitForResponse(
        (r) => r.url().includes('/api/v1/i18n/admin/translation/') && r.request().method() === 'POST',
        { timeout: 10000 }
      );

      await modal.locator('button[type="submit"]:has-text("Save")').click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');

      const response = await responsePromise;
      expect(response.ok()).toBeTruthy();

      await expect(keysPanel).toContainText(value, { timeout: 10000 });
    }
  );
});
