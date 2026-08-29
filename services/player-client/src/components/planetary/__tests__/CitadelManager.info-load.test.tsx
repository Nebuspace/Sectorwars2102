// @vitest-environment jsdom
/**
 * CitadelManager — info load refusal (LEG-2851).
 * GET .../citadel → citadelAPI.getInfo(planetId) error surfaces GS detail.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getInfo } = vi.hoisted(() => ({
  getInfo: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  citadelAPI: {
    getInfo,
    upgrade: vi.fn(),
  },
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

import CitadelManager, { formatCitadelLoadError } from '../CitadelManager';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('CitadelManager — info load refusal', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getInfo.mockReset();
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

  it('surfaces 400 server detail when citadel info load is refused', async () => {
    getInfo.mockRejectedValue(
      apiRequestError(400, 'You do not own this planet'),
    );

    await act(async () => {
      root.render(<CitadelManager planetId="planet-1" playerCredits={100_000} />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.citadel-error')?.textContent).toContain(
        'You do not own this planet',
      );
    });
  });

  it('formatCitadelLoadError hides bare API Error status codes', () => {
    expect(formatCitadelLoadError(apiRequestError(400))).toBe(
      'Failed to load citadel info',
    );
  });
});
