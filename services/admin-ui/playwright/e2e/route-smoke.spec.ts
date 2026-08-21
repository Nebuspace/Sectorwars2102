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
      { path: '/first-login-conversations', title: 'First Login Conversations' },
      { path: '/fleets', title: 'Fleet Management' },
      { path: '/economy', title: 'Economy Dashboard' },
      { path: '/multi-account', title: 'Multi-Account Review' },
      { path: '/scopes', title: 'Admin Scopes' },
      { path: '/ai-trading', title: 'AI Trading Intelligence' },
      { path: '/universe/planets', title: 'Planets Manager' },
      { path: '/universe/stations', title: 'Stations Manager' },
      { path: '/universe/warptunnels', title: 'Warp Tunnels Manager' },
      { path: '/tradedocks', title: 'TradeDock management' },
      { path: '/medals', title: 'Medal Admin' },
      { path: '/events', title: 'Event Management' },
      { path: '/security', title: 'Security Dashboard' },
      { path: '/analytics', title: 'Advanced Analytics' },
      { path: '/players', title: 'Players' },
      { path: '/translations', title: 'Translation Management' },
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

    test('/review-queue aliases to Admin Action Log review tab (LEG-1640 / LEG-77 residual)', async ({
      page,
    }) => {
      await page.goto('/review-queue', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL((url) => {
        return url.pathname === '/audit' && url.searchParams.get('tab') === 'review';
      });
      await expect(page.locator('h1.page-title')).toHaveText('Admin Action Log');
      await expect(page.getByRole('tab', { name: 'Review queue' })).toHaveAttribute(
        'aria-selected',
        'true'
      );
    });
  });
});
