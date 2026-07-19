# End-to-End (E2E) Tests

This directory contains end-to-end tests for Sector Wars 2102 using Playwright. Tests run against the dev host over Tailscale (see the repo root `CLAUDE.md` for the execution environment).

## Test Structure

```
e2e_tests/
├── admin/ui/                  # Admin UI test files
│   ├── admin-ui-dashboard.spec.ts
│   ├── admin-ui-login.spec.ts
│   ├── admin-ui-sector-editing.spec.ts
│   ├── admin-ui-sector-editing-authenticated.spec.ts
│   ├── admin-ui-universe-generation.spec.ts
│   ├── admin-ui-user-management.spec.ts
│   ├── deep-link-basename.spec.ts
│   ├── faction-mutations.spec.ts
│   └── translation-key-edit.spec.ts
├── bang/                      # sw2102-bang galaxy-generation admin flow
│   ├── concurrent-admins.spec.ts
│   ├── generate-full-flow.spec.ts
│   ├── iteration.spec.ts
│   ├── partial-state-recovery.spec.ts
│   ├── sse-log.spec.ts
│   └── wipe-regenerate.spec.ts
├── player/ui/                 # Player client test files
│   └── player-ui.spec.ts
├── fixtures/                  # Shared test fixtures
│   └── auth.fixtures.ts       # Authentication fixtures
├── utils/                     # Shared utility functions
│   ├── auth.utils.ts          # Authentication utilities
│   ├── bang-helpers.ts        # sw2102-bang test helpers
│   └── test_account_manager.ts # Test account management
├── foundation-sprint/         # Shared helpers for foundation-sprint specs
│   └── test-helpers.ts
├── global-setup.ts            # Global test setup
├── global-teardown.ts         # Global test teardown
├── playwright.config.ts       # Playwright configuration
├── tsconfig.json              # TypeScript configuration for tests
└── run_all_tests.sh           # Convenience wrapper to run all tests
```

> **Note:** `playwright.config.ts`'s two projects (`admin-tests`, `player-tests`) match `**/admin/**/*.spec.ts` and `**/player/**/*.spec.ts` only. The `bang/` specs are not matched by either project's `testMatch`, and passing a `bang/` path on the command line does not bypass that — `npx playwright test bang/` currently resolves to "0 tests in 0 files". They exist and are protected, but they are not runnable through this config until a project (or a broadened `testMatch`) is added for them.

## Running Tests

### Using the run_all_tests.sh script

```bash
./run_all_tests.sh
```

### Using npx directly (from project root)

```bash
# Run all tests
npx playwright test -c e2e_tests/playwright.config.ts

# Run specific test projects
npx playwright test -c e2e_tests/playwright.config.ts --project=admin-tests
npx playwright test -c e2e_tests/playwright.config.ts --project=player-tests

# Run tests with UI mode for debugging
npx playwright test -c e2e_tests/playwright.config.ts --ui

# Run with HTML reporter for detailed results
npx playwright test -c e2e_tests/playwright.config.ts --reporter=html

# Run specific test file
npx playwright test -c e2e_tests/playwright.config.ts admin/ui/admin-ui-login.spec.ts
```

## Writing New Tests

When adding new tests:

1. Follow the existing directory structure
2. Use the shared fixtures and utilities for common functionality
3. Group related tests with `test.describe()`
4. Use clear and descriptive test names

### Example Test

```typescript
import { test, expect } from '@playwright/test';
import { test as authTest } from '../../../fixtures/auth.fixtures';
import { loginAsAdmin } from '../../../utils/auth.utils';

test.describe('Feature Group', () => {
  // Use auth fixture when needed
  authTest.beforeEach(async ({ page, adminCredentials }) => {
    await loginAsAdmin(page, adminCredentials);
  });

  authTest('should perform specific action', async ({ page }) => {
    // Test steps
    await page.goto('/path');
    await page.click('button');

    // Assertions
    await expect(page.locator('.result')).toBeVisible();
  });
});
```

## Test Artifacts

### Screenshots
- Screenshots are automatically captured on test failures
- Stored in `/e2e_tests/screenshots/` (auto-generated, gitignored)
- Configured via `outputDir` in `playwright.config.ts`

### Test Reports
- HTML reports generated in `/e2e_tests/playwright-reports/` (auto-generated, gitignored)
- View with: `npx playwright show-report e2e_tests/playwright-reports`

### Traces
- Traces captured on first retry for debugging
- Can be viewed in Playwright trace viewer

## Prerequisites

Before running E2E tests:

1. Ensure the dev stack is up and reachable (Tailscale connectivity to the dev host)
2. Ensure databases are migrated and seeded
3. Verify services are accessible at the expected URLs (`ADMIN_UI_URL`, `PLAYER_UI_URL`, `API_URL` env vars, defaulting to `localhost:3001` / `localhost:3000` / `localhost:8080`)
4. Install Playwright browsers (one-time setup): `npx playwright install chromium --with-deps`

## Debugging Failed Tests

1. **Check screenshots**: Look in `/e2e_tests/screenshots/` for failure screenshots
2. **View traces**: Use `npx playwright show-trace` on trace files
3. **Run with UI mode**: Use `--ui` flag for interactive debugging
4. **Check service logs**: via `docker compose logs <service-name>` on the remote dev host
