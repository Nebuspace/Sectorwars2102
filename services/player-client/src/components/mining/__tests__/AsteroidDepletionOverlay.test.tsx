// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import AsteroidDepletionOverlay from '../AsteroidDepletionOverlay';

describe('AsteroidDepletionOverlay', () => {
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
    vi.useRealTimers();
  });

  it('renders GS band labels without inventing cut-points', async () => {
    await act(async () => {
      root.render(
        <AsteroidDepletionOverlay
          readout={{
            band: 'fresh',
            replenish_eta: null,
            replenish_hours: null,
          }}
        />,
      );
    });
    const band = container.querySelector('[data-testid="asteroid-depletion-band"]');
    expect(band?.textContent).toBe('Fresh');
    expect(container.querySelector('[data-testid="asteroid-depletion-countdown"]')).toBeNull();
    expect(container.querySelector('[data-testid="asteroid-depletion-empty"]')).toBeNull();
  });

  it('shows a real-time replenish countdown on Heavy when GS sends eta', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-27T12:00:00.000Z'));
    const eta = '2026-08-27T12:01:30.000Z';
    await act(async () => {
      root.render(
        <AsteroidDepletionOverlay
          readout={{
            band: 'heavy',
            replenish_eta: eta,
            replenish_hours: 168,
          }}
        />,
      );
    });
    const countdown = container.querySelector('[data-testid="asteroid-depletion-countdown"]');
    expect(container.querySelector('[data-testid="asteroid-depletion-band"]')?.textContent).toBe(
      'Heavy',
    );
    expect(countdown?.textContent).toContain('1m 30s');
  });

  it('honest empty when readout is null (non-asteroid / absent)', async () => {
    await act(async () => {
      root.render(<AsteroidDepletionOverlay readout={null} />);
    });
    const empty = container.querySelector('[data-testid="asteroid-depletion-empty"]');
    expect(empty).not.toBeNull();
    expect(container.querySelector('[data-testid="asteroid-depletion-band"]')).toBeNull();
    expect(container.querySelector('[data-testid="asteroid-depletion-countdown"]')).toBeNull();
    expect(container.querySelector('[data-testid="asteroid-depletion-overlay"]')?.getAttribute(
      'data-empty',
    )).toBe('true');
  });
});
