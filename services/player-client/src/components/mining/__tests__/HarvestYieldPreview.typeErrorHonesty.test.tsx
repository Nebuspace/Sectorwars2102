// @vitest-environment jsdom
/**
 * LEG-3766 Soft-ORDER — HarvestYieldPreview typeErrorHonesty.
 * LEG-4059 Soft-ORDER — HTTP 403/429 densify (invent=0).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockPreview = vi.fn();

vi.mock('../../../services/api', () => ({
  miningAPI: {
    getYieldPreview: (...args: unknown[]) => mockPreview(...args),
  },
}));

import HarvestYieldPreview, { harvestGateMessage } from '../HarvestYieldPreview';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('harvestGateMessage TypeError densify (LEG-3766)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = harvestGateMessage(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Yield preview failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch non-TypeError', () => {
    expect(harvestGateMessage(new Error('Network Error'))).toMatch(/Yield preview failed/i);
    expect(harvestGateMessage(new Error('Failed to fetch'))).toMatch(/Yield preview failed/i);
    expect(harvestGateMessage(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves known gate reason codes', () => {
    expect(harvestGateMessage('no_mining_laser')).toContain('No mining laser equipped');
  });
});

describe('HarvestYieldPreview load transport collapse densify (LEG-3766)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockPreview.mockReset();
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

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('preview %s surfaces honest fallback without raw transport text', async (_label, err) => {
    mockPreview.mockRejectedValue(err);
    await act(async () => {
      root.render(<HarvestYieldPreview shipId="ship-9" />);
    });
    await flush();
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toMatch(/Yield preview failed/i);
    expect(alert?.textContent).not.toMatch(/Failed to fetch/i);
    expect(alert?.textContent).not.toMatch(/TypeError/i);
    expect(alert?.textContent).not.toMatch(/Network Error/i);
  });
});

describe('harvestGateMessage 403/429 densify (LEG-4059)', () => {
  it('maps 403/429 without raw transport leakage', () => {
    expect(harvestGateMessage(apiRequestError(403))).toBe(
      'Access denied — you cannot preview harvest yield right now.',
    );
    expect(harvestGateMessage(apiRequestError(403, 'no_mining_laser'))).toContain('No mining laser');
    expect(harvestGateMessage(apiRequestError(429))).toMatch(/rate limit/i);
    expect(harvestGateMessage(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(harvestGateMessage(apiRequestError(403))).not.toMatch(/API Error/i);
    expect(harvestGateMessage(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
