// @vitest-environment jsdom
/**
 * LEG-3459 / LEG-3652 Soft-ORDER — EmpireProductionDashboard transport-error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getOwnedPlanets } = vi.hoisted(() => ({
  getOwnedPlanets: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  gameAPI: { planetary: { getOwnedPlanets } },
}));

import EmpireProductionDashboard, {
  formatEmpireProductionLoadError,
} from '../EmpireProductionDashboard';

const FALLBACK = 'Failed to load production data';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('EmpireProductionDashboard TypeError densify (LEG-3459)', () => {
  it('formatEmpireProductionLoadError falls back on TypeError network collapse', () => {
    const text = formatEmpireProductionLoadError(new TypeError('Failed to fetch'), FALLBACK);
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatEmpireProductionLoadError(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(formatEmpireProductionLoadError(new Error('Failed to fetch'), FALLBACK)).toBe(FALLBACK);
    expect(formatEmpireProductionLoadError(new Error('Network Error'), FALLBACK)).not.toMatch(
      /Network Error/i,
    );
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatEmpireProductionLoadError(new Error('production_offline'), FALLBACK)).toBe(
      'production_offline',
    );
  });
});

describe('EmpireProductionDashboard load transport collapse densify (LEG-3652)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getOwnedPlanets.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('load Network Error surfaces honest fallback without raw transport text', async () => {
    getOwnedPlanets.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<EmpireProductionDashboard />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.sb-production-error');
    expect(errorEl?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('load Failed to fetch (non-TypeError) surfaces honest fallback without raw transport text', async () => {
    getOwnedPlanets.mockRejectedValue(new Error('Failed to fetch'));

    await act(async () => {
      root.render(<EmpireProductionDashboard />);
    });
    await act(async () => {
      await flush();
    });

    const errorEl = container.querySelector('.sb-production-error');
    expect(errorEl?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
