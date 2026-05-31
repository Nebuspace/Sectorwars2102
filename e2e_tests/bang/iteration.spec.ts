/**
 * E2E: iterate on multiple seeds before committing.
 *
 * Form must retain other typed values across preview attempts so the
 * admin can rapidly cycle seeds without re-entering region type / sectors.
 */
import { expect } from '@playwright/test';
import { test as authTest } from '../fixtures/auth.fixtures';
import { loginAsAdmin } from '../utils/auth.utils';
import {
  clickCommit,
  clickPreview,
  fillCommonTier,
  gotoBangPage,
  waitForLogPanel,
  waitForPreviewStats,
} from '../utils/bang-helpers';

authTest.describe('Bang Galaxy — seed iteration', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await gotoBangPage(page);
  });

  authTest('preview 42 → 43 → 44 → commit 43', async ({ page }) => {
    // Seed 42
    await fillCommonTier(page, {
      seed: 42,
      sectors: 200,
      regionType: 'player_owned',
      galaxyName: 'Iteration Galaxy',
    });
    await clickPreview(page);
    await waitForPreviewStats(page);

    // Seed 43 — only the seed field changes
    await page.locator('input[type="number"]').first().fill('43');
    await clickPreview(page);
    await waitForPreviewStats(page);

    // Seed 44
    await page.locator('input[type="number"]').first().fill('44');
    await clickPreview(page);
    await waitForPreviewStats(page);

    // Now commit on seed 43 — set it back, then commit. The other typed
    // fields (sectors, region, galaxy name) must still be intact.
    await page.locator('input[type="number"]').first().fill('43');

    // Verify sectors field still says 200
    const sectorsValue = await page
      .locator('input[type="number"]')
      .nth(1)
      .inputValue();
    expect(sectorsValue).toBe('200');

    // Verify galaxy name is still "Iteration Galaxy"
    const nameValue = await page
      .locator('input[type="text"]')
      .first()
      .inputValue();
    expect(nameValue).toBe('Iteration Galaxy');

    await clickCommit(page);
    await waitForLogPanel(page);
  });
});
