import { test as authTest, expect } from '../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../utils/auth.utils';

/**
 * WO-ADM-ECONDASH-FE — EconomyDashboard mutation controls.
 *
 * Covers price-alert create/delete against the DB-backed PriceAlert CRUD
 * (admin_economy.py POST /create-alert, DELETE /alerts/{alert_id}), the
 * price-intervention control that used to fire straight off a bare
 * prompt(), and the inject_liquidity supply-injection control. Every
 * mutation now routes through the shared in-shell confirm dialog
 * (.confirm-dialog / useConfirm) and a visible toast (the NH6 standard),
 * not the native window.confirm/alert.
 *
 * NOTE: at the time this spec was authored, the deployed dev host still
 * serves the pre-fix build. This spec is expected to go green on the
 * next deploy, not necessarily today.
 */

authTest.describe('Admin UI - EconomyDashboard mutation controls (WO-ADM-ECONDASH-FE)', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
    await page.goto('/economy', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('h1.page-title')).toHaveText('Economy Dashboard');
    await page.waitForSelector('.market-table tbody tr', { timeout: 15000 });
    await page.waitForSelector('.alert-manage-section', { timeout: 15000 });
  });

  authTest('create-alert form: cancel issues zero network requests', async ({ page }) => {
    const createAlertRequests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/admin/economy/create-alert')) {
        createAlertRequests.push(`${request.method()} ${request.url()}`);
      }
    });

    await page.selectOption('#alert-station', { index: 1 });
    await page.selectOption('#alert-commodity', { index: 1 });
    await page.fill('#alert-threshold', '15');

    await page.click('.alert-create-form button[type="submit"]');
    await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
    await page.click('.confirm-dialog .confirm-btn.cancel');
    await expect(page.locator('.confirm-dialog')).toHaveCount(0);

    expect(createAlertRequests).toEqual([]);
  });

  authTest(
    'create-alert form: confirm POSTs the exact PriceAlertCreateRequest body and shows a success toast',
    async ({ page }) => {
      await page.selectOption('#alert-station', { index: 1 });
      await page.selectOption('#alert-commodity', { index: 1 });
      await page.selectOption('#alert-type', 'price_spike');
      await page.fill('#alert-threshold', '15');

      const stationId = await page.locator('#alert-station').inputValue();
      const commodity = await page.locator('#alert-commodity').inputValue();

      const requestPromise = page.waitForRequest(
        (request) =>
          request.url().includes('/api/v1/admin/economy/create-alert') && request.method() === 'POST'
      );

      await page.click('.alert-create-form button[type="submit"]');
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');

      const request = await requestPromise;
      expect(request.postDataJSON()).toEqual({
        station_id: stationId,
        commodity,
        alert_type: 'price_spike',
        threshold_value: 15
      });

      await expect(page.locator('.toast-success')).toBeVisible({ timeout: 5000 });
      await expect(page.locator('.created-alerts-list .created-alert-item').first()).toBeVisible();
    }
  );

  authTest(
    'per-row delete: cancel issues zero DELETE requests; confirm DELETEs /alerts/{alert_id} and shows a success toast',
    async ({ page }) => {
      // Seed a session-tracked alert via the create flow — the backend has
      // no GET/list endpoint for persistent PriceAlert rows (see the
      // CreatedPriceAlert doc comment in EconomyDashboard.tsx), so the row
      // to delete has to come from a create in this same test.
      await page.selectOption('#alert-station', { index: 1 });
      await page.selectOption('#alert-commodity', { index: 1 });
      await page.fill('#alert-threshold', '15');

      const createResponsePromise = page.waitForResponse(
        (r) => r.url().includes('/api/v1/admin/economy/create-alert') && r.request().method() === 'POST'
      );
      await page.click('.alert-create-form button[type="submit"]');
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');
      const createResponse = await createResponsePromise;
      const { alert_id } = await createResponse.json();

      const row = page.locator('.created-alert-item').first();
      await expect(row).toBeVisible({ timeout: 5000 });

      const deleteRequests: string[] = [];
      page.on('request', (request) => {
        if (request.method() === 'DELETE' && request.url().includes('/admin/economy/alerts/')) {
          deleteRequests.push(request.url());
        }
      });

      // Cancel path first — must fire zero DELETE requests, row stays put.
      await row.locator('button.action-btn.delete').click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.cancel');
      await expect(page.locator('.confirm-dialog')).toHaveCount(0);
      expect(deleteRequests).toEqual([]);
      await expect(row).toBeVisible();

      // Confirm path — DELETEs the exact alert_id returned by create.
      const deleteRequestPromise = page.waitForRequest(
        (request) =>
          request.method() === 'DELETE' &&
          request.url().includes(`/api/v1/admin/economy/alerts/${alert_id}`)
      );
      await row.locator('button.action-btn.delete').click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.danger');
      await deleteRequestPromise;

      await expect(page.locator('.toast-success')).toBeVisible({ timeout: 5000 });
    }
  );

  authTest(
    'price intervention: confirm summarizes old->new price and POSTs price_adjustment only after confirm',
    async ({ page }) => {
      const firstRow = page.locator('.market-table tbody tr').first();
      const interveneBtn = firstRow.locator('button.action-btn.intervention');
      await expect(interveneBtn).toBeVisible({ timeout: 10000 });

      page.once('dialog', (dialog) => dialog.accept('999'));

      const requestPromise = page.waitForRequest(
        (request) =>
          request.url().includes('/api/v1/admin/economy/intervention') && request.method() === 'POST'
      );

      await interveneBtn.click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await expect(page.locator('.confirm-dialog .confirm-message')).toContainText('999');
      await page.click('.confirm-dialog .confirm-btn.primary');

      const request = await requestPromise;
      const body = request.postDataJSON();
      expect(body.intervention_type).toBe('price_adjustment');
      expect(body.parameters.new_price).toBe(999);

      await expect(page.locator('.toast-success')).toBeVisible({ timeout: 5000 });
    }
  );

  authTest('price intervention: cancel issues zero /intervention requests', async ({ page }) => {
    const firstRow = page.locator('.market-table tbody tr').first();
    const interveneBtn = firstRow.locator('button.action-btn.intervention');
    await expect(interveneBtn).toBeVisible({ timeout: 10000 });

    const interventionRequests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/admin/economy/intervention')) {
        interventionRequests.push(`${request.method()} ${request.url()}`);
      }
    });

    page.once('dialog', (dialog) => dialog.accept('999'));

    await interveneBtn.click();
    await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
    await page.click('.confirm-dialog .confirm-btn.cancel');
    await expect(page.locator('.confirm-dialog')).toHaveCount(0);

    expect(interventionRequests).toEqual([]);
  });

  authTest(
    'supply injection: confirm gates the inject_liquidity POST; cancel issues zero requests',
    async ({ page }) => {
      const firstRow = page.locator('.market-table tbody tr').first();
      const injectBtn = firstRow.locator('button.action-btn.inject');
      await expect(injectBtn).toBeVisible({ timeout: 10000 });

      const injectRequests: string[] = [];
      page.on('request', (request) => {
        if (request.url().includes('/admin/economy/intervention')) {
          injectRequests.push(`${request.method()} ${request.url()}`);
        }
      });

      // Cancel path: accept the quantity prompt, decline the confirm.
      page.once('dialog', (dialog) => dialog.accept('50'));
      await injectBtn.click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.cancel');
      await expect(page.locator('.confirm-dialog')).toHaveCount(0);
      expect(injectRequests).toEqual([]);

      // Confirm path: POSTs inject_liquidity with the entered quantity.
      page.once('dialog', (dialog) => dialog.accept('50'));
      const requestPromise = page.waitForRequest(
        (request) =>
          request.url().includes('/api/v1/admin/economy/intervention') && request.method() === 'POST'
      );
      await injectBtn.click();
      await expect(page.locator('.confirm-dialog')).toBeVisible({ timeout: 5000 });
      await page.click('.confirm-dialog .confirm-btn.primary');

      const request = await requestPromise;
      const body = request.postDataJSON();
      expect(body.intervention_type).toBe('inject_liquidity');
      expect(body.parameters.station_id).toBeTruthy();
      expect(Object.values(body.parameters.resources)[0]).toBe(50);

      await expect(page.locator('.toast-success')).toBeVisible({ timeout: 5000 });
    }
  );
});
