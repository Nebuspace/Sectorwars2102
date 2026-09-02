// @vitest-environment jsdom
/**
 * LEG-3643 Soft-ORDER — CoupledColonistSliders allocation persist error honesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import CoupledColonistSliders, {
  ALLOCATION_PERSIST_FALLBACK,
  formatCoupledColonistAllocError,
  type RoleAllocation,
} from '../CoupledColonistSliders';

const ALLOCATIONS: RoleAllocation = { fuel: 10, organics: 10, equipment: 10 };

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatCoupledColonistAllocError TypeError densify (LEG-3643)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatCoupledColonistAllocError(new TypeError('Failed to fetch'));
    expect(text).toBe(ALLOCATION_PERSIST_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatCoupledColonistAllocError(new Error('Network Error'))).toBe(
      ALLOCATION_PERSIST_FALLBACK,
    );
    expect(formatCoupledColonistAllocError(new Error('Failed to fetch'))).toBe(
      ALLOCATION_PERSIST_FALLBACK,
    );
    expect(formatCoupledColonistAllocError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('falls back on not-implemented stub copy', () => {
    expect(formatCoupledColonistAllocError(new Error('Not implemented'))).toBe(
      ALLOCATION_PERSIST_FALLBACK,
    );
    expect(formatCoupledColonistAllocError(new Error('Not implemented'))).not.toMatch(/not implemented/i);
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatCoupledColonistAllocError(new Error('overflow_denied'))).toBe('overflow_denied');
  });
});

describe('formatCoupledColonistAllocError 403/429 densify (LEG-4028)', () => {
  it('surfaces 403/429 without raw status codes', () => {
    expect(formatCoupledColonistAllocError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatCoupledColonistAllocError(apiRequestError(403, 'alloc_denied'))).toBe('alloc_denied');
    expect(formatCoupledColonistAllocError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatCoupledColonistAllocError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatCoupledColonistAllocError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});

describe('CoupledColonistSliders allocation error display (LEG-3643)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const renderSliders = async (error?: string | null) => {
    await act(async () => {
      root.render(
        <CoupledColonistSliders
          allocations={ALLOCATIONS}
          productionRates={{ fuel: 100, organics: 100, equipment: 100 }}
          budget={40}
          totalColonists={30}
          onSetAll={vi.fn()}
          error={error}
        />,
      );
    });
  };

  it('renders honest allocation error copy in role=alert without transport leakage', async () => {
    await renderSliders(ALLOCATION_PERSIST_FALLBACK);

    const alert = container.querySelector('.cp-slider-error[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toMatch(/Allocation update failed/i);
    expect(alert?.textContent).not.toMatch(/Network Error/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
    expect(alert?.textContent).not.toMatch(/not implemented/i);
  });

  it('densifies raw Network Error prop at display time', async () => {
    await renderSliders('Network Error');

    const alert = container.querySelector('.cp-slider-error[role="alert"]');
    expect(alert?.textContent).toMatch(/Allocation update failed/i);
    expect(alert?.textContent).not.toMatch(/Network Error/i);
  });

  it('densifies raw Failed to fetch prop at display time', async () => {
    await renderSliders('Failed to fetch');

    const alert = container.querySelector('.cp-slider-error[role="alert"]');
    expect(alert?.textContent).toMatch(/Allocation update failed/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
  });
});
