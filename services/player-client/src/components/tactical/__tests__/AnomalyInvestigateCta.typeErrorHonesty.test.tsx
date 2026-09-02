// @vitest-environment jsdom
/**
 * LEG-3765 Soft-ORDER — AnomalyInvestigateCta typeErrorHonesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mockInvestigateAnomaly = vi.fn();

vi.mock('../../../services/api', () => ({
  playerAPI: {
    investigateAnomaly: (...args: unknown[]) => mockInvestigateAnomaly(...args),
  },
}));

import AnomalyInvestigateCta, {
  formatAnomalyInvestigateError,
} from '../AnomalyInvestigateCta';

describe('formatAnomalyInvestigateError TypeError densify (LEG-3765)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatAnomalyInvestigateError(new TypeError('Failed to fetch'));
    expect(text).toBe('Investigation failed. Please try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(formatAnomalyInvestigateError(new Error('Network Error'))).toBe(
      'Investigation failed. Please try again.',
    );
    expect(formatAnomalyInvestigateError(new Error('Failed to fetch'))).toBe(
      'Investigation failed. Please try again.',
    );
    expect(formatAnomalyInvestigateError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves server detail for non-TypeError errors', () => {
    const err = Object.assign(new Error('sector_locked'), {
      response: { data: { detail: 'Sector is locked by another captain.' } },
    });
    expect(formatAnomalyInvestigateError(err)).toBe('Sector is locked by another captain.');
  });
});

describe('formatAnomalyInvestigateError 403/429 densify (LEG-4020)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatAnomalyInvestigateError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatAnomalyInvestigateError(apiRequestError(403, 'investigate_denied'))).toBe(
      'investigate_denied',
    );
    expect(formatAnomalyInvestigateError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatAnomalyInvestigateError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatAnomalyInvestigateError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});

describe('AnomalyInvestigateCta transport collapse densify (LEG-3765)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.clearAllMocks();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('investigate TypeError surfaces fallback without raw transport text', async () => {
    mockInvestigateAnomaly.mockRejectedValue(new TypeError('Failed to fetch'));
    await act(async () => {
      root.render(<AnomalyInvestigateCta sectorId={42} sectorType="ANOMALY" />);
    });
    const btn = container.querySelector(
      '[data-testid="anomaly-investigate-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    const status = container.querySelector('[data-testid="anomaly-investigate-status"]');
    expect(status?.textContent).toBe('Investigation failed. Please try again.');
    expect(status?.textContent).not.toMatch(/Failed to fetch/i);
    expect(status?.textContent).not.toMatch(/TypeError/i);
  });
});
