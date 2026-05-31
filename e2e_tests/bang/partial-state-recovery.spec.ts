/**
 * E2E: orphan-job recovery on next startup.
 *
 * Goal: confirm that a job left in RUNNING state across a process restart
 * gets flipped to FAILED with `error_message='orphaned at startup'`.
 *
 * **KNOWN LIMITATION (flagged for Phase 4A coordination)**: There is no
 * clean public endpoint to forcibly mark a job RUNNING with a backdated
 * `started_at`. Without that, we can't simulate "worker killed mid-job"
 * deterministically from a Playwright test. We could:
 *   (a) Hit the DB directly from this spec — requires DATABASE_URL in
 *       the Playwright env, which isn't currently set up.
 *   (b) Add a debug-only ``POST /api/v1/admin/galaxy/jobs/{id}/_force_running``
 *       endpoint guarded by ``settings.ENVIRONMENT == "testing"``.
 *   (c) Defer the assertion to the gameserver pytest suite
 *       (``tests/integration/test_bang_orphan_recovery.py`` covers it).
 *
 * Until (b) ships, this spec runs a *smoke* check: it verifies that a
 * fresh page load after starting a job correctly renders the history
 * table. The real orphan-recovery assertion lives in pytest.
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

authTest.describe('Bang Galaxy — partial state recovery (smoke)', () => {
  authTest('history table renders after a fresh navigation', async ({
    page,
    adminCredentials,
  }) => {
    await loginAsAdmin(page, adminCredentials);
    await gotoBangPage(page);

    await fillCommonTier(page, {
      ...DEFAULT_PAYLOAD,
      galaxyName: 'Recovery Smoke',
    });
    await clickCommit(page);
    await waitForLogPanel(page);
    await waitForHistoryRow(page);

    // Simulate "process restart" by reloading the page. The history table
    // should re-render the recently-queued job's row.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await waitForHistoryRow(page);

    // The full orphan-recovery assertion (started_at < now() - 5min →
    // FAILED with 'orphaned at startup') lives in
    // services/gameserver/tests/integration/test_bang_orphan_recovery.py.
    expect(true).toBe(true);
  });
});
