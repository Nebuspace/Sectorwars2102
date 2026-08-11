import { test as authTest, expect } from '../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../utils/auth.utils';

/**
 * WO-BUILD-ADMIN-UI-ECONOMY-LEVERS-PANEL — unified Economy Levers on /economy.
 */
authTest.describe('Admin UI - Economy Levers panel', () => {
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await page.route('**/api/v1/admin/economy/levers', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            regions: [
              {
                id: '11111111-1111-1111-1111-111111111111',
                name: 'terra',
                display_name: 'Terran Space',
                tax_rate: 0.1,
                starting_credits: 1000,
                status: 'active',
              },
            ],
            ship_specs: [
              { type: 'scout', base_cost: 5000, is_npc_only: false },
            ],
            upgrades: [
              {
                type: 'engine',
                base_cost: 5000,
                cost_multiplier: 2.0,
                description: 'test',
              },
            ],
          }),
        });
        return;
      }
      await route.continue();
    });

    await loginAsAdmin(page, adminCredentials);
    await page.goto('/economy', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('h1.page-title')).toHaveText('Economy Dashboard');
  });

  authTest('levers panel visible with region / ship / upgrade sections', async ({ page }) => {
    const panel = page.getByTestId('economy-levers-panel');
    await expect(panel).toBeVisible({ timeout: 15000 });
    await expect(panel.getByRole('heading', { name: 'Economy Levers' })).toBeVisible();
    await expect(panel.getByText('Terran Space')).toBeVisible();
    await expect(panel.getByLabel('Tax rate for terra')).toHaveValue('10.0');
    await expect(panel.getByLabel('Base cost for scout')).toHaveValue('5000');
    await expect(panel.getByLabel('Base cost for upgrade engine')).toHaveValue('5000');

    // Scroll Law: primary levers block is above market table chrome
    const panelBox = await panel.boundingBox();
    expect(panelBox).not.toBeNull();
    expect(panelBox!.y).toBeLessThan(700);
  });

  authTest('saving region lever PATCHes admin economy levers API', async ({ page }) => {
    const panel = page.getByTestId('economy-levers-panel');
    await expect(panel).toBeVisible({ timeout: 15000 });

    let patched = false;
    await page.route(
      '**/api/v1/admin/economy/levers/regions/11111111-1111-1111-1111-111111111111',
      async (route) => {
        if (route.request().method() === 'PATCH') {
          patched = true;
          const body = route.request().postDataJSON();
          expect(body.tax_rate).toBeCloseTo(0.12, 5);
          expect(body.starting_credits).toBe(1000);
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              region_id: '11111111-1111-1111-1111-111111111111',
              tax_rate: 0.12,
              starting_credits: 1000,
              applied: {},
            }),
          });
          return;
        }
        await route.continue();
      }
    );

    await panel.getByLabel('Tax rate for terra').fill('12');
    await panel.getByRole('button', { name: 'Save' }).first().click();
    await expect.poll(() => patched).toBe(true);
  });
});
