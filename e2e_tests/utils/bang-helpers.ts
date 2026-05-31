/**
 * Shared helpers for the bang-galaxy Playwright suite.
 *
 * The bang admin surface lives at `/universe/bang` (sidebar entry seeded
 * by `Phase 3 admin-ui` PRs). All bang e2e specs:
 *
 *  1. Log in as admin (real OAuth/login, not mock — per the test-pattern
 *     audit; falls back to mock auth if the gameserver is unreachable).
 *  2. Navigate to `/universe/bang`.
 *  3. Use these helpers to drive the form / wipe / poll workflow.
 *
 * The helpers prefer Playwright's user-facing locators (text, role, label)
 * over CSS classes so the suite survives stylistic refactors of the UI.
 */
import { Page, expect, APIRequestContext } from '@playwright/test';

export const BANG_PAGE_PATH = '/universe/bang';
export const API_PREFIX = '/api/v1/admin';

/** Default preview / commit config used across specs. */
export interface BangFormPayload {
  seed: number;
  sectors: number;
  regionType: 'player_owned' | 'terran_space' | 'central_nexus';
  galaxyName?: string;
}

export const DEFAULT_PAYLOAD: BangFormPayload = {
  seed: 42,
  sectors: 200,
  regionType: 'player_owned',
  galaxyName: 'E2E Test Galaxy',
};

/** Navigate to the Bang Galaxy admin page, asserting the form renders. */
export async function gotoBangPage(page: Page): Promise<void> {
  await page.goto(BANG_PAGE_PATH, { waitUntil: 'domcontentloaded' });
  // The form fieldset legend is the most stable anchor.
  await expect(
    page.locator('form.galaxy-generation-form, h2.form-title').first(),
  ).toBeVisible({ timeout: 10000 });
}

/** Fill the three Common-tier fields. Sets galaxy_name if provided. */
export async function fillCommonTier(
  page: Page,
  payload: BangFormPayload,
): Promise<void> {
  // Seed
  await page
    .locator('label:has-text("Seed") input[type="number"], input[type="number"]')
    .first()
    .fill(String(payload.seed));
  // Sectors
  const sectorInputs = page.locator('input[type="number"]');
  const sectorCount = await sectorInputs.count();
  if (sectorCount >= 2) {
    await sectorInputs.nth(1).fill(String(payload.sectors));
  }
  // Region type
  const regionSelect = page.locator('select').first();
  await regionSelect.selectOption(payload.regionType);
  // Galaxy name (optional)
  if (payload.galaxyName) {
    await page
      .locator('input[type="text"]')
      .first()
      .fill(payload.galaxyName);
  }
}

/** Click the form's "Preview" button. */
export async function clickPreview(page: Page): Promise<void> {
  await page
    .getByRole('button', { name: /preview/i })
    .first()
    .click();
}

/** Click the form's "Generate" / "Commit" submit button. */
export async function clickCommit(page: Page): Promise<void> {
  await page
    .getByRole('button', { name: /generate|commit|submit/i })
    .first()
    .click();
}

/** Wait until the preview stats panel renders something we can read. */
export async function waitForPreviewStats(page: Page): Promise<void> {
  // The stats card is rendered conditionally below the form; its hallmark
  // is the word "diameter" or "stats" or "validator".
  await page.waitForSelector(
    'text=/diameter|sectors|validator|stats/i',
    { timeout: 15000 },
  );
}

/** Wait for the SSE log panel to appear once a job is in progress. */
export async function waitForLogPanel(page: Page): Promise<void> {
  await page.waitForSelector(
    '.generation-log-panel, [data-testid="bang-log-panel"], pre',
    { timeout: 15000 },
  );
}

/** Wait for a history row to appear in the table. */
export async function waitForHistoryRow(
  page: Page,
  jobId?: string,
): Promise<void> {
  if (jobId) {
    await page.waitForSelector(`tr:has-text("${jobId}"), [data-job-id="${jobId}"]`, {
      timeout: 20000,
    });
  } else {
    await page.waitForSelector('table tbody tr, .history-row', {
      timeout: 20000,
    });
  }
}

/** Fire a bang POST /jobs request via the API (bypasses UI; used for
 * the concurrency spec to time two admins to within ~50ms). */
export async function postJobViaApi(
  request: APIRequestContext,
  baseUrl: string,
  token: string,
  payload: BangFormPayload,
): Promise<{ status: number; body: unknown }> {
  const resp = await request.post(`${baseUrl}${API_PREFIX}/galaxy/jobs`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      config: {
        seed: payload.seed,
        sectors: payload.sectors,
        region_type: payload.regionType,
      },
      galaxy_name: payload.galaxyName,
    },
  });
  return { status: resp.status(), body: await resp.json().catch(() => null) };
}

/** Pull the bearer token out of localStorage (set during loginAsAdmin). */
export async function readAccessToken(page: Page): Promise<string> {
  return (await page.evaluate(() => localStorage.getItem('accessToken') || '')) as string;
}

/** Fire a wipe DELETE via the API — used by partial-state recovery spec. */
export async function deleteGalaxyViaApi(
  request: APIRequestContext,
  baseUrl: string,
  token: string,
  galaxyId: string,
  galaxyName: string,
): Promise<number> {
  const resp = await request.delete(
    `${baseUrl}${API_PREFIX}/galaxy/${galaxyId}`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'X-Confirm-Galaxy-Name': galaxyName,
      },
    },
  );
  return resp.status();
}
