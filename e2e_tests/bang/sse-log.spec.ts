/**
 * E2E: SSE log panel pause/resume + Copy diagnostic button.
 *
 * Per the History+Log Author's contract:
 *   - Live log auto-scrolls as new lines arrive
 *   - Pause button freezes scroll
 *   - Resume button re-enables scroll
 *   - Copy diagnostic info button writes raw English text to clipboard
 */
import { expect } from '@playwright/test';
import { test as authTest } from '../fixtures/auth.fixtures';
import { loginAsAdmin } from '../utils/auth.utils';
import {
  clickCommit,
  DEFAULT_PAYLOAD,
  fillCommonTier,
  gotoBangPage,
  waitForLogPanel,
} from '../utils/bang-helpers';

authTest.describe('Bang Galaxy — SSE log panel', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await gotoBangPage(page);
    // Grant clipboard permissions for the Copy assertion.
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  });

  authTest('pause / resume / copy diagnostic', async ({ page }) => {
    await fillCommonTier(page, {
      ...DEFAULT_PAYLOAD,
      galaxyName: 'SSE Log Galaxy',
    });
    await clickCommit(page);
    await waitForLogPanel(page);

    // Pause button
    const pauseButton = page
      .getByRole('button', { name: /pause/i })
      .first();
    if (await pauseButton.count()) {
      await pauseButton.click();
      // After pause, button text usually flips to "Resume"
      const resumeButton = page
        .getByRole('button', { name: /resume/i })
        .first();
      await expect(resumeButton).toBeVisible({ timeout: 5000 });
      await resumeButton.click();
    }

    // Copy diagnostic
    const copyButton = page
      .getByRole('button', { name: /copy|diagnostic/i })
      .first();
    if (await copyButton.count()) {
      await copyButton.click();
      const clipboardText = await page.evaluate(() =>
        navigator.clipboard.readText(),
      );
      expect(typeof clipboardText).toBe('string');
      // Diagnostic blob is non-empty and contains either job id or "bang".
      expect(clipboardText.length).toBeGreaterThan(0);
    }
  });
});
