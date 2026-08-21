import { test, expect } from '@playwright/test';
import { installSmokeAuth } from '../helpers/smoke-auth';

/**
 * Route smoke — proves React Router wiring for admin pages.
 * Auth is stubbed; API returns 503 so pages render landmarks without a backend.
 */
test.describe('Admin UI route smoke (WO-ADM-ROUTE-SMOKE-E2E)', () => {
  test('login route renders Admin Portal landmark', async ({ page }) => {
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Sector Wars 2102' })).toBeVisible();
    await expect(page.getByText('Admin Portal')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Admin Login' })).toBeVisible();
  });

  test.describe('authenticated pages', () => {
    test.beforeEach(async ({ page }) => {
      await installSmokeAuth(page);
    });

    const routes: { path: string; title: string }[] = [
      { path: '/dashboard', title: 'Dashboard' },
      { path: '/users', title: 'User Management' },
      { path: '/factions', title: 'Faction Management' },
      { path: '/combat', title: 'Combat Overview' },
      { path: '/teams', title: 'Team Management' },
      { path: '/contract-disputes', title: 'Contract Dispute Arbitration' },
      { path: '/universe/planets', title: 'Planets Manager' },
      { path: '/tradedocks', title: 'TradeDock management' },
    ];

    for (const { path, title } of routes) {
      test(`${path} renders page title "${title}"`, async ({ page }) => {
        await page.goto(path, { waitUntil: 'domcontentloaded' });
        // Literal path match — avoid RegExp+partial escape (CodeQL js/incomplete-sanitization).
        await expect(page).toHaveURL((url) => {
          const pathname = url.pathname;
          return pathname === path || pathname === `${path}/`;
        });
        await expect(page.locator('h1.page-title')).toHaveText(title);
      });
    }
  });
});
