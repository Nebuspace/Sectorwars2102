#!/bin/bash
# Script to run all E2E tests for Sector Wars 2102
# Targets the dev host over Tailscale per playwright.config.ts's baseURL defaults.

echo "Running Sector Wars 2102 E2E Tests"
echo "=================================="

REPORT_URL="http://localhost:9323"
echo "Test reports will be available at: $REPORT_URL"
echo ""

# Set working directory to the project root
SCRIPT_DIR="$(dirname "$0")"
cd "$SCRIPT_DIR" || exit 1

echo "Current working directory: $(pwd)"

# Playwright browsers are a one-time setup step, not a per-run install.
# If this fails with a "browser not found" error, run: npx playwright install chromium --with-deps

# Run Playwright tests
echo "Running Playwright tests for Admin UI and Player Client..."
npx playwright test -c playwright.config.ts

# Display test results
echo ""
echo "Test execution completed!"
echo "View HTML report at: $REPORT_URL"

# Exit with success status
exit 0
