// @vitest-environment jsdom
/**
 * LEG-3798 Soft-ORDER — WindshieldFlightContext telemetry TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../AutopilotContext', () => ({
  useAutopilot: () => ({ status: 'idle', abort: vi.fn() }),
}));

import {
  WindshieldFlightProvider,
  useWindshieldFlight,
  WINDSHIELD_FLIGHT_TELEMETRY_FALLBACK,
  formatWindshieldFlightTelemetryError,
  type WindshieldFlightContextValue,
} from '../WindshieldFlightContext';

let latest: WindshieldFlightContextValue | null = null;

function Harness() {
  latest = useWindshieldFlight();
  return latest.telemetryError ? (
    <div data-testid="telemetry-error">{latest.telemetryError}</div>
  ) : null;
}

const assertNoTransportLeak = (text: string) => {
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/Network Error/i);
};

describe('formatWindshieldFlightTelemetryError (LEG-3798)', () => {
  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('collapses %s to fallback without raw transport text', (_label, err) => {
    const text = formatWindshieldFlightTelemetryError(err, WINDSHIELD_FLIGHT_TELEMETRY_FALLBACK);
    expect(text).toBe(WINDSHIELD_FLIGHT_TELEMETRY_FALLBACK);
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail', () => {
    expect(
      formatWindshieldFlightTelemetryError(
        new Error('sector contents unavailable'),
        WINDSHIELD_FLIGHT_TELEMETRY_FALLBACK,
      ),
    ).toBe('sector contents unavailable');
  });
});

describe('WindshieldFlightContext telemetry typeErrorHonesty (LEG-3798)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    latest = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <Harness />
        </WindshieldFlightProvider>,
      );
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('reportTelemetryFailure %s surfaces player-safe fallback', async (_label, err) => {
    await act(async () => {
      latest!.reportTelemetryFailure(err);
    });

    const node = container.querySelector('[data-testid="telemetry-error"]');
    expect(node?.textContent).toBe(WINDSHIELD_FLIGHT_TELEMETRY_FALLBACK);
    assertNoTransportLeak(node?.textContent ?? '');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});

describe('formatWindshieldFlightTelemetryError 403/429 densify (LEG-4097)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };
  it('surfaces 403/429 without raw status codes', () => {
    const fallback = WINDSHIELD_FLIGHT_TELEMETRY_FALLBACK;
    expect(formatWindshieldFlightTelemetryError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatWindshieldFlightTelemetryError(apiRequestError(403, 'telemetry_denied'), fallback)).toBe(
      'telemetry_denied',
    );
    expect(formatWindshieldFlightTelemetryError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatWindshieldFlightTelemetryError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatWindshieldFlightTelemetryError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});

