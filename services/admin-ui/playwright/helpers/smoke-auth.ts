import type { Page } from '@playwright/test';

/** Minimal JWT (unverified) with a future `exp` so AuthContext keeps it. */
export function fakeAccessToken(): string {
  const encode = (obj: object) =>
    Buffer.from(JSON.stringify(obj))
      .toString('base64')
      .replace(/=+$/, '')
      .replace(/\+/g, '-')
      .replace(/\//g, '_');
  const header = encode({ alg: 'none', typ: 'JWT' });
  const payload = encode({
    sub: 'smoke-admin',
    exp: Math.floor(Date.now() / 1000) + 7200,
  });
  return `${header}.${payload}.smoke`;
}

const SMOKE_USER = {
  id: 'smoke-admin',
  username: 'smoke',
  email: 'smoke@test.local',
  is_admin: true,
  is_active: true,
  last_login: null,
};

/**
 * Seed a logged-in session and stub API calls so protected pages render
 * without a live gameserver. Unknown APIs return 503 so callers that use
 * allSettled/try-catch degrade honestly instead of crashing on bad shapes.
 */
export async function installSmokeAuth(page: Page): Promise<void> {
  const token = fakeAccessToken();

  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/auth/me')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(SMOKE_USER),
      });
      return;
    }
    // Soft-ORDER #1768 — TranslationManagement GET /i18n/admin/languages/all then
    // languages.filter before PageHeader. The generic /i18n/ bang stub below also
    // matches this path; a 200 object is not an array → throw, no h1.
    if (url.includes('/i18n/admin/languages/all')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            code: 'en',
            name: 'English',
            nativeName: 'English',
            direction: 'ltr',
            isActive: true,
            completionPercentage: 100,
          },
        ]),
      });
      return;
    }
    // Soft-ORDER #1772 — BangGalaxyPage title is i18n `bang.page.title` (HTTP backend).
    // Stub admin ns so `/universe/bang` smoke can assert "Bang Galaxy" without a live GS.
    if (url.includes('/i18n/')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          bang: {
            page: {
              title: 'Bang Galaxy',
              subtitle: 'Generate, preview, and manage galaxies via the sw2102-bang engine.',
              tabForm: 'Generate',
              tabHistory: 'History',
            },
          },
        }),
      });
      return;
    }
    // Soft-ORDER #1770 — RegionalGovernorDashboard h1 is behind `if (!region)`.
    // Generic 503 on my-region leaves region=null ("No region found", no h1).
    // Exact path only — do not swallow /my-region/stats|policies|… (those 503-catch).
    // Do not touch RegionalGovernorDashboard.tsx (file-serial Soft-HOLD #764).
    try {
      const pathname = new URL(url).pathname.replace(/\/+$/, '');
      if (pathname.endsWith('/regions/my-region')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'reg-1',
            name: 'sol',
            display_name: 'Sol Reach',
            owner_id: 'p1',
            subscription_tier: 'free',
            status: 'active',
            governance_type: 'autocracy',
            tax_rate: 0.1,
            voting_threshold: 0.51,
            economic_specialization: '',
            total_sectors: 12,
            active_players_30d: 4,
            total_trade_volume: 0,
            starting_credits: 1000,
            starting_ship: 'basic',
            language_pack: {},
            aesthetic_theme: {},
            trade_bonuses: {},
          }),
        });
        return;
      }
    } catch {
      // fall through to 503 stub
    }
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'admin-ui route-smoke stub' }),
    });
  });

  await page.addInitScript((accessToken) => {
    localStorage.setItem('accessToken', accessToken);
    localStorage.setItem('refreshToken', accessToken);
  }, token);
}
