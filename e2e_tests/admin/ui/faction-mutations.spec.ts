import { v4 as uuidv4 } from 'uuid';
import { Locator } from '@playwright/test';
import { test as authTest, expect } from '../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../utils/auth.utils';

/**
 * WO-NEON-NH6-ADMIN-MUTATIONS — Faction Management safe-kernel mutations.
 *
 * Covers create / edit / territory / reputation against the already-registered
 * gameserver routes (admin_factions.py), each gated by the shared in-shell
 * confirm dialog (.confirm-dialog / useConfirm). Destructive faction DELETE is
 * intentionally NOT wired here — that surface stays gated pending Max's ruling
 * (see DECISIONS) — so one test asserts its absence.
 */

const BASE_URL = 'http://localhost:3001';

// Form fields have no htmlFor/id pairing to their labels — scope by the
// .form-group that contains a matching .form-label, mirroring the DOM shape
// FactionManagement.tsx actually renders.
const fieldByLabel = (scope: Locator, label: string): Locator =>
  scope
    .locator('.form-group')
    .filter({ has: scope.page().locator('.form-label', { hasText: label }) })
    .locator('input, select, textarea');

authTest.describe('Admin UI - Faction Management mutations', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await page.goto(`${BASE_URL}/factions`);
    await page.waitForSelector('.faction-management', { timeout: 15000 });
    await page.waitForLoadState('networkidle').catch(() => {});
  });

  authTest('renders zero destructive controls (no faction DELETE)', async ({ page }) => {
    const deleteControls = page.locator('button, a').filter({ hasText: /delete/i });
    expect(await deleteControls.count()).toBe(0);
  });

  authTest(
    'creating a faction issues POST /api/v1/admin/factions/ and the row appears without reload',
    async ({ page }) => {
      const name = `E2E Faction ${uuidv4().substring(0, 8)}`;

      await page.click('button:has-text("+ Create Faction")');
      await expect(page.locator('.modal')).toBeVisible({ timeout: 5000 });

      const modal = page.locator('.modal');
      await fieldByLabel(modal, 'Name').fill(name);

      const responsePromise = page.waitForResponse(
        (r) => r.url().includes('/api/v1/admin/factions/') && r.request().method() === 'POST',
        { timeout: 10000 }
      );

      await modal.locator('button[type="submit"]:has-text("Create Faction")').click();

      // The safe-kernel convention: every mutation is gated by the shared
      // in-shell confirm dialog (useConfirm), not the native window.confirm.
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');

      const response = await responsePromise;
      expect(response.ok()).toBeTruthy();

      await expect(page.locator('.faction-table')).toContainText(name, { timeout: 10000 });
    }
  );

  authTest(
    'editing a faction issues PUT /api/v1/admin/factions/{id} and the table reflects the change',
    async ({ page }) => {
      const firstRow = page.locator('.faction-table tbody tr').first();
      await expect(firstRow).toBeVisible({ timeout: 10000 });

      await firstRow.locator('button:has-text("Edit")').click();
      await expect(page.locator('.modal')).toBeVisible({ timeout: 5000 });

      const modal = page.locator('.modal');
      const newDescription = `E2E edit ${uuidv4().substring(0, 8)}`;
      await fieldByLabel(modal, 'Description').fill(newDescription);

      const responsePromise = page.waitForResponse(
        (r) => /\/api\/v1\/admin\/factions\/[0-9a-fA-F-]+$/.test(r.url()) && r.request().method() === 'PUT',
        { timeout: 10000 }
      );

      await modal.locator('button[type="submit"]:has-text("Save Changes")').click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');

      const response = await responsePromise;
      expect(response.ok()).toBeTruthy();

      await expect(page.locator('.faction-table')).toContainText(newDescription, { timeout: 10000 });
    }
  );

  authTest(
    'updating territory issues PUT /api/v1/admin/factions/{id}/territory',
    async ({ page }) => {
      const firstRow = page.locator('.faction-table tbody tr').first();
      await expect(firstRow).toBeVisible({ timeout: 10000 });

      await firstRow.locator('button:has-text("Territory")').click();
      await expect(page.locator('.modal')).toBeVisible({ timeout: 5000 });

      const responsePromise = page.waitForResponse(
        (r) => r.url().includes('/territory') && r.request().method() === 'PUT',
        { timeout: 10000 }
      );

      await page.locator('.modal button[type="submit"]:has-text("Save Territory")').click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');

      const response = await responsePromise;
      // No seeded sector UUID is available at e2e time; this proves the
      // mutation is wired end-to-end (request fires with the right
      // method/URL), not that any pasted sector actually exists server-side.
      console.log(`Territory PUT status: ${response.status()}`);
    }
  );

  authTest(
    'adjusting reputation issues PUT /api/v1/admin/factions/{id}/reputation',
    async ({ page }) => {
      const firstRow = page.locator('.faction-table tbody tr').first();
      await expect(firstRow).toBeVisible({ timeout: 10000 });

      await firstRow.locator('button:has-text("Reputation")').click();
      await expect(page.locator('.modal')).toBeVisible({ timeout: 5000 });

      const modal = page.locator('.modal');
      // A syntactically-valid but almost certainly nonexistent player UUID —
      // same rationale as the territory test above.
      await fieldByLabel(modal, 'Player ID').fill('00000000-0000-4000-8000-000000000000');

      const responsePromise = page.waitForResponse(
        (r) => r.url().includes('/reputation') && r.request().method() === 'PUT',
        { timeout: 10000 }
      );

      await modal.locator('button[type="submit"]:has-text("Apply Change")').click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');

      const response = await responsePromise;
      console.log(`Reputation PUT status: ${response.status()}`);
    }
  );

  authTest('a disabled .faction-btn renders dimmed and non-interactive', async ({ page }) => {
    const refreshBtn = page.locator('button.faction-btn:has-text("Refresh")');
    await expect(refreshBtn).toBeVisible({ timeout: 10000 });

    // Force the disabled state directly to assert the CSS contract
    // deterministically, independent of any in-flight request timing.
    await refreshBtn.evaluate((el: HTMLButtonElement) => {
      el.disabled = true;
    });

    const opacity = await refreshBtn.evaluate((el) => getComputedStyle(el).opacity);
    const cursor = await refreshBtn.evaluate((el) => getComputedStyle(el).cursor);

    expect(opacity).toBe('0.5');
    expect(cursor).toBe('not-allowed');
  });
});
