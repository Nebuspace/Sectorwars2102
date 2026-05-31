/**
 * E2E: wipe (typed-name confirm) → regenerate workflow.
 *
 * The wipe dialog requires the admin to type the exact galaxy name to
 * enable the confirm button. After wipe, the page returns to the empty
 * state and a fresh generation can be queued.
 */
import { expect } from '@playwright/test';
import { test as authTest } from '../fixtures/auth.fixtures';
import { loginAsAdmin } from '../utils/auth.utils';
import {
  clickCommit,
  DEFAULT_PAYLOAD,
  fillCommonTier,
  gotoBangPage,
  waitForHistoryRow,
  waitForLogPanel,
} from '../utils/bang-helpers';

authTest.describe('Bang Galaxy — wipe + regenerate', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await gotoBangPage(page);
  });

  authTest('commit → wipe (typed-name) → confirm gone', async ({ page }) => {
    const galaxyName = 'Wipe Test Galaxy';
    await fillCommonTier(page, { ...DEFAULT_PAYLOAD, galaxyName });
    await clickCommit(page);
    await waitForLogPanel(page);
    await waitForHistoryRow(page);

    // Open the wipe dialog
    const wipeButton = page
      .getByRole('button', { name: /wipe|delete|destroy/i })
      .first();
    await expect(wipeButton).toBeVisible({ timeout: 15000 });
    await wipeButton.click();

    // The dialog should appear with a confirm input + disabled button
    const confirmInput = page.locator(
      'input[name*="confirm"], input[placeholder*="name"]',
    );
    await expect(confirmInput.first()).toBeVisible({ timeout: 5000 });

    // The confirm button is disabled until the name matches.
    const confirmButton = page
      .getByRole('button', { name: /confirm|delete/i })
      .last();
    await expect(confirmButton).toBeDisabled();

    // Type the wrong name first — button stays disabled.
    await confirmInput.first().fill('Wrong Galaxy Name');
    await expect(confirmButton).toBeDisabled();

    // Type the correct name → button enables → click.
    await confirmInput.first().fill(galaxyName);
    await expect(confirmButton).toBeEnabled({ timeout: 5000 });
    await confirmButton.click();

    // After deletion, the form area should be back (no galaxy header showing
    // the bang_version / seed of the now-gone galaxy).
    await expect(
      page.locator('text=' + galaxyName),
    ).toHaveCount(0, { timeout: 15000 });
  });
});
