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
      { path: '/events', title: 'Event Management' },
      { path: '/security', title: 'Security Dashboard' },
      { path: '/analytics', title: 'Advanced Analytics' },
      { path: '/players', title: 'Players' },
      { path: '/translations', title: 'Translation Management' },
      // Soft-ORDER #1769–#1772 — tip-PRESENT App routes missing from smoke
      { path: '/colonies', title: 'Colonization Management' },
      { path: '/messages', title: 'Message Moderation' },
      { path: '/nexus', title: 'Central Nexus Management' },
      { path: '/regional-governor', title: 'Regional Governor Dashboard' },
      { path: '/audit', title: 'Admin Action Log' },
      { path: '/universe/sectors', title: 'Sectors Management' },
      { path: '/universe/bang', title: 'Bang Galaxy' },
      // Soft-ORDER LEG-1714/#1790 — bare /sectors (distinct from /universe/sectors alias)
      { path: '/sectors', title: 'Sectors Management' },
    ];

    for (const { path, title } of routes) {
      test(`${path} renders page title "${title}"`, async ({ page }) => {
        await page.goto(path, { waitUntil: 'domcontentloaded' });
        // Literal path match — avoid RegExp+partial escape (CodeQL js/incomplete-sanitization).
        await expect(page).toHaveURL((url) => {
          const pathname = url.pathname;
          return pathname === path || pathname === `${path}/`;
        });
        // level-1 heading: PageHeader (h1.page-title) and bare <h1> landmarks both qualify
        await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible();
      });
    }

    // Soft-ORDER LEG-1714/#1790 — UniverseManager landmark is h2 "No Universe"
    // (or galaxy name). The authenticated loop asserts level-1 headings only.
    test('/universe renders UniverseManager galaxy landmark (h2)', async ({ page }) => {
      await page.goto('/universe', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL((url) => {
        const pathname = url.pathname;
        return pathname === '/universe' || pathname === '/universe/';
      });
      await expect(
        page.getByRole('heading', { name: 'No Universe', level: 2 })
      ).toBeVisible();
    });

    test('/review-queue aliases to Admin Action Log review tab (LEG-1640 / LEG-77 residual)', async ({
      page,
    }) => {
      await page.goto('/review-queue', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL((url) => {
        return url.pathname === '/audit' && url.searchParams.get('tab') === 'review';
      });
      await expect(page.getByRole('heading', { name: 'Admin Action Log', level: 1 })).toBeVisible();
      await expect(page.getByRole('tab', { name: 'Review queue' })).toHaveAttribute(
        'aria-selected',
        'true'
      );
    });
  });
});
