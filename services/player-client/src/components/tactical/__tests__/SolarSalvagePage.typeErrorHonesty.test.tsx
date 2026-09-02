// @vitest-environment jsdom
/**
 * LEG-3741 Soft-ORDER — SolarSalvagePage TypeError/network densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SectorWreck } from '../../../services/api';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockSalvageWreck = vi.fn();

vi.mock('../../../services/api', () => ({
  sectorAPI: {
    salvageWreck: (...args: unknown[]) => mockSalvageWreck(...args),
  },
}));

import SolarSalvagePage, { formatSalvageError } from '../pages/SolarSalvagePage';

const FALLBACK = 'Salvage failed';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const WRECK: SectorWreck = {
  id: 'wreck-1',
  original_owner_id: null,
  original_owner_name: 'Crimson Corsair',
  destroyed_ship_type: 'LIGHT_FREIGHTER',
  cause: 'combat',
  created_at: '2026-01-01T00:00:00Z',
  age_seconds: 120,
  cargo: { ore: 14, equipment: 2 },
  would_flag_suspect: false,
};

describe('SolarSalvagePage TypeError densify (LEG-3741)', () => {
  it('formatSalvageError falls back on TypeError network collapse', () => {
    const text = formatSalvageError(new TypeError('Failed to fetch'));
    expect(text).toBe(FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatSalvageError(new Error('Network Error'))).toBe(FALLBACK);
    expect(formatSalvageError(new Error('Failed to fetch'))).toBe(FALLBACK);
    expect(formatSalvageError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves non-generic server detail', () => {
    expect(formatSalvageError(new Error('wreck locked'))).toBe('wreck locked');
  });
});

describe('SolarSalvagePage salvage transport collapse densify (LEG-3741)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockSalvageWreck.mockReset();
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

  it('salvage TypeError surfaces fallback without raw transport text', async () => {
    mockSalvageWreck.mockRejectedValue(new TypeError('Failed to fetch'));

    await act(async () => {
      root.render(<SolarSalvagePage wrecks={[WRECK]} onSalvaged={vi.fn()} />);
    });
    await act(async () => {
      await flush();
    });

    await act(async () => {
      container.querySelector('.solar-salvage-wreck-row')!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
      await flush();
    });
    await act(async () => {
      container.querySelector('.solar-salvage-btn')!.dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
      await flush();
    });

    const msg = container.querySelector('.solar-salvage-msg.err');
    expect(msg?.textContent).toBe(FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });
});
