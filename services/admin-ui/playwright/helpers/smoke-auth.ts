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
