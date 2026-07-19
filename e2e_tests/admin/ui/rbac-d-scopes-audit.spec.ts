import { expect } from '@playwright/test';
import { test as authTest } from '../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../utils/auth.utils';

const BASE_PATH = (process.env.ADMIN_UI_BASE_PATH || '').replace(/\/$/, '');

authTest.describe('Admin UI — RBAC D scopes + action log (WO-RBAC-D)', () => {
  authTest('scopes page deep-link renders grant UI landmarks', async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await page.goto(`${BASE_PATH}/scopes`, { waitUntil: 'domcontentloaded' });

    await expect(page.locator('h1.page-title')).toHaveText('Admin Scopes');
    // Either the holders panel or a forbidden/scope-missing alert — never a blank crash.
    const holders = page.getByRole('region', { name: 'Scope holders' });
    const forbidden = page.getByRole('alert');
    await expect(holders.or(forbidden).first()).toBeVisible({ timeout: 15000 });
  });

  authTest('action log page is read-only (no edit/delete affordance)', async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await page.goto(`${BASE_PATH}/audit`, { waitUntil: 'domcontentloaded' });

    await expect(page.locator('h1.page-title')).toHaveText('Admin Action Log');
    await expect(page.getByRole('form', { name: 'Action log filters' }).or(page.getByRole('alert')).first()).toBeVisible({
      timeout: 15000,
    });

    await expect(page.getByRole('button', { name: /delete|edit|modify/i })).toHaveCount(0);
  });
});
