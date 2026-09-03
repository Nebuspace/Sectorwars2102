// @vitest-environment jsdom
/**
 * PirateHoldingCaptureControl — LEG-4188 capture residual (product: LEG-4154).
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const captureHolding = vi.fn();
const onCaptured = vi.fn();

vi.mock('../../../services/api', () => ({
  pirateHoldingsAPI: {
    captureHolding: (...args: unknown[]) => captureHolding(...args),
  },
}));

import PirateHoldingCaptureControl from '../PirateHoldingCaptureControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const HOLDING_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('PirateHoldingCaptureControl', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    captureHolding.mockReset();
    onCaptured.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('calls captureHolding with the holding id', async () => {
    captureHolding.mockResolvedValue({
      holding_id: HOLDING_ID,
      captured_at: '2026-09-03T16:00:00Z',
      owner_team_id: null,
    });

    await act(async () => {
      root.render(
        <PirateHoldingCaptureControl holdingId={HOLDING_ID} onCaptured={onCaptured} />,
      );
      await flush();
    });

    const btn = container.querySelector(
      '[data-testid="pirate-holding-capture-btn"]',
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(container.querySelector('[data-testid="pirate-holding-capture-control"]')).toBeTruthy();

    await act(async () => {
      btn.click();
      await flush();
    });

    expect(captureHolding).toHaveBeenCalledWith(HOLDING_ID);
    expect(onCaptured).toHaveBeenCalledTimes(1);
  });

  it('shows team-owned success copy when owner_team_id is present', async () => {
    captureHolding.mockResolvedValue({
      holding_id: HOLDING_ID,
      captured_at: '2026-09-03T16:00:00Z',
      owner_team_id: 'team-fed-1',
    });

    await act(async () => {
      root.render(
        <PirateHoldingCaptureControl holdingId={HOLDING_ID} onCaptured={onCaptured} />,
      );
      await flush();
    });

    await act(async () => {
      (container.querySelector('[data-testid="pirate-holding-capture-btn"]') as HTMLButtonElement).click();
      await flush();
    });

    const text = container.querySelector(
      '[data-testid="pirate-holding-capture-success"]',
    )?.textContent;
    expect(text).toMatch(/Holding captured/i);
    expect(text).toMatch(/Now owned by your team/i);
  });

  it('does not invent a team on success when owner_team_id is absent', async () => {
    captureHolding.mockResolvedValue({
      holding_id: HOLDING_ID,
      captured_at: '2026-09-03T16:00:00Z',
      owner_team_id: null,
    });

    await act(async () => {
      root.render(
        <PirateHoldingCaptureControl holdingId={HOLDING_ID} onCaptured={onCaptured} />,
      );
      await flush();
    });

    await act(async () => {
      (container.querySelector('[data-testid="pirate-holding-capture-btn"]') as HTMLButtonElement).click();
      await flush();
    });

    const text = container.querySelector(
      '[data-testid="pirate-holding-capture-success"]',
    )?.textContent;
    expect(text).toMatch(/Holding captured/i);
    expect(text).not.toMatch(/team/i);
    expect(container.querySelector('[data-testid="pirate-holding-capture-btn"]')).toBeNull();
  });

  it('surfaces 409 lock-lost copy and keeps the capture control usable', async () => {
    captureHolding.mockRejectedValue(apiRequestError(409));

    await act(async () => {
      root.render(
        <PirateHoldingCaptureControl holdingId={HOLDING_ID} onCaptured={onCaptured} />,
      );
      await flush();
    });

    await act(async () => {
      (container.querySelector('[data-testid="pirate-holding-capture-btn"]') as HTMLButtonElement).click();
      await flush();
    });

    const err = container.querySelector('[data-testid="pirate-holding-capture-err"]')?.textContent;
    expect(err).toMatch(/Combat lock lost/i);
    expect(err).toMatch(/already be captured/i);
    expect(err).not.toMatch(/\b409\b/);
    expect(onCaptured).not.toHaveBeenCalled();
    const btn = container.querySelector(
      '[data-testid="pirate-holding-capture-btn"]',
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    expect(container.querySelector('[data-testid="pirate-holding-capture-control"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="pirate-holding-capture-success"]')).toBeNull();
  });

  it('surfaces generic capture failure without a raw status code', async () => {
    captureHolding.mockRejectedValue(apiRequestError(500));

    await act(async () => {
      root.render(
        <PirateHoldingCaptureControl holdingId={HOLDING_ID} onCaptured={onCaptured} />,
      );
      await flush();
    });

    await act(async () => {
      (container.querySelector('[data-testid="pirate-holding-capture-btn"]') as HTMLButtonElement).click();
      await flush();
    });

    const err = container.querySelector('[data-testid="pirate-holding-capture-err"]')?.textContent;
    expect(err).toMatch(/Capture failed/i);
    expect(err).not.toMatch(/\b500\b/);
    expect(onCaptured).not.toHaveBeenCalled();
  });

  it('disables the capture button while the request is in-flight', async () => {
    let release!: (value: unknown) => void;
    captureHolding.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );

    await act(async () => {
      root.render(
        <PirateHoldingCaptureControl holdingId={HOLDING_ID} onCaptured={onCaptured} />,
      );
      await flush();
    });

    const btn = container.querySelector(
      '[data-testid="pirate-holding-capture-btn"]',
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('aria-busy')).toBe('true');
    expect(btn.textContent).toMatch(/…/);
    expect(onCaptured).not.toHaveBeenCalled();

    await act(async () => {
      release({
        holding_id: HOLDING_ID,
        captured_at: null,
        owner_team_id: null,
      });
      await flush();
    });
  });
});
