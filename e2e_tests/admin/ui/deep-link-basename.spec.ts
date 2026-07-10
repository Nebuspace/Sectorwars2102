import { test, expect } from '@playwright/test';
import { test as authTest } from '../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../utils/auth.utils';

// WO-NEON-NH8: on stage the admin UI is served under /admin/ (nginx +
// VITE_BASE) while local/dev serves it at the root. Set
// ADMIN_UI_BASE_PATH=/admin when running this spec against stage; it
// defaults to '' (root) for the local dev server, matching the WO's
// "Dev (base '/')" acceptance case.
const BASE_PATH = (process.env.ADMIN_UI_BASE_PATH || '').replace(/\/$/, '');

test.describe('Admin UI - deep-link basename (WO-NEON-NH8)', () => {
  authTest('root redirects to dashboard and dashboard renders (no basename regression)', async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);

    await page.goto(`${BASE_PATH}/`, { waitUntil: 'domcontentloaded' });

    await expect(page).toHaveURL(new RegExp(`${BASE_PATH}/dashboard$`));
    await expect(page.locator('h1.page-title')).toHaveText('Dashboard');
  });

  authTest('a fresh-tab deep link to a non-dashboard route renders that route, not the /dashboard fallback', async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);

    // A hard navigation (not client-side routing) — this is exactly the
    // path that fell through to the '*' -> /dashboard fallback before the
    // Router basename fix (missing basename meant no route matched).
    await page.goto(`${BASE_PATH}/factions`, { waitUntil: 'domcontentloaded' });

    await expect(page.locator('h1.page-title')).toHaveText('Faction Management');
    expect(page.url()).not.toContain('/dashboard');
  });

  test('a logged-out deep link redirects to login and returns to the destination after auth', async ({ page }) => {
    // Start from a clean, logged-out slate.
    await page.goto(`${BASE_PATH}/login`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });

    await page.goto(`${BASE_PATH}/users`, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(new RegExp(`${BASE_PATH}/login$`));

    await page.fill('#username, [name="username"], input[type="text"]', 'admin');
    await page.fill('#password, [name="password"], input[type="password"]', 'admin');
    await page.click('.login-button, button[type="submit"], [role="button"]');

    // The preserved destination (not /dashboard) should be where we land.
    await expect(page).toHaveURL(new RegExp(`${BASE_PATH}/users$`), { timeout: 15000 });
    await expect(page.locator('h1.page-title')).toHaveText('User Management');
  });
});

test.describe('Admin UI - unknown-route 404 (WO-ADM-FALLBACK-404)', () => {
  authTest('an authenticated visit to an unknown route renders the 404 view showing the attempted path, not a silent /dashboard redirect', async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);

    await page.goto(`${BASE_PATH}/definitely-not-a-route`, { waitUntil: 'domcontentloaded' });

    // The old behaviour silently redirected every unknown path to /dashboard,
    // which is exactly what let dead-link regressions go unnoticed. Assert
    // both that we did NOT land on /dashboard and that the 404 view names
    // the actual attempted path (proving it's not a generic error screen).
    expect(page.url()).not.toContain('/dashboard');
    await expect(page.locator('h1.page-title')).toHaveText('Page Not Found');
    await expect(page.getByText('/definitely-not-a-route', { exact: false })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Return to Dashboard' })).toBeVisible();
  });

  test('a logged-out visit to an unknown route still redirects to login with state.from preserved, landing on the 404 view after auth', async ({ page }) => {
    // Start from a clean, logged-out slate (mirrors the known-route case above).
    await page.goto(`${BASE_PATH}/login`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });

    await page.goto(`${BASE_PATH}/definitely-not-a-route`, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(new RegExp(`${BASE_PATH}/login$`));

    await page.fill('#username, [name="username"], input[type="text"]', 'admin');
    await page.fill('#password, [name="password"], input[type="password"]', 'admin');
    await page.click('.login-button, button[type="submit"], [role="button"]');

    // state.from carries the ORIGINAL unknown path back through login, so
    // the admin lands there (now authenticated) and sees the 404 view --
    // not /dashboard.
    await expect(page).toHaveURL(new RegExp(`${BASE_PATH}/definitely-not-a-route$`), { timeout: 15000 });
    await expect(page.locator('h1.page-title')).toHaveText('Page Not Found');
  });
});
