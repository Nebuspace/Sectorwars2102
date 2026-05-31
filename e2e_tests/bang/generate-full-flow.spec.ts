/**
 * E2E: full preview → commit → live log → history workflow.
 *
 * Mirrors the admin's happy-path: open the form, set parameters, click
 * Preview, see stats, click Commit, watch SSE log, then verify the job
 * appears in the history table.
 */
import { expect } from '@playwright/test';
import { test as authTest } from '../fixtures/auth.fixtures';
import { loginAsAdmin } from '../utils/auth.utils';
import {
  clickCommit,
  clickPreview,
  DEFAULT_PAYLOAD,
  fillCommonTier,
  gotoBangPage,
  waitForHistoryRow,
  waitForLogPanel,
  waitForPreviewStats,
} from '../utils/bang-helpers';

authTest.describe('Bang Galaxy — full flow', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await gotoBangPage(page);
  });

  authTest('preview → commit → log → history', async ({ page }) => {
    await fillCommonTier(page, {
      ...DEFAULT_PAYLOAD,
      galaxyName: 'Full Flow Galaxy',
    });

    // Preview
    await clickPreview(page);
    await waitForPreviewStats(page);

    // Commit
    await clickCommit(page);

    // Live log + history
    await waitForLogPanel(page);
    await waitForHistoryRow(page);

    // Sanity: the page now has a visible job-status badge
    const statusBadge = page.locator(
      'text=/RUNNING|COMPLETE|PENDING|FAILED/i',
    );
    await expect(statusBadge.first()).toBeVisible({ timeout: 20000 });

    await page.screenshot({ path: 'bang-full-flow.png' });
  });
});
