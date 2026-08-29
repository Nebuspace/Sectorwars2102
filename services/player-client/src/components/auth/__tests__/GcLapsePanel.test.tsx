// @vitest-environment jsdom
/**
 * GcLapsePanel — WO-WIRE-GC-LAPSE-SELF-SERVICE.
 * Pins GET status → relocate POST when relocation_available.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { getStatus, emergencyRelocate, refreshPlayerState } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  emergencyRelocate: vi.fn(),
  refreshPlayerState: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../../services/api', () => ({
  gcLapseAPI: {
    getStatus: (...a: unknown[]) => getStatus(...a),
    emergencyRelocate: (...a: unknown[]) => emergencyRelocate(...a),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ refreshPlayerState }),
}));

import GcLapsePanel, { formatGcLapseRelocateError } from '../GcLapsePanel';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('GcLapsePanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getStatus.mockReset();
    emergencyRelocate.mockReset().mockResolvedValue({ outcome: 'gc_emergency_relocation' });
    refreshPlayerState.mockClear();
    vi.stubGlobal('confirm', vi.fn(() => true));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  it('renders nothing when not lapsed', async () => {
    getStatus.mockResolvedValue({
      lapsed: false,
      gc_lapsed_at: null,
      relocation_available: false,
      foreign_holdings: [],
    });
    await act(async () => {
      root.render(<GcLapsePanel />);
    });
    await flush();
    expect(container.querySelector('[data-testid="gc-lapse-panel"]')).toBeNull();
  });

  it('lists holdings and POSTs emergency relocate', async () => {
    getStatus.mockResolvedValue({
      lapsed: true,
      gc_lapsed_at: '2026-08-01T00:00:00Z',
      relocation_available: true,
      foreign_holdings: [
        {
          asset_type: 'planet',
          asset_id: 'p-1',
          name: 'Outpost',
          region_id: 'r-2',
          sector_id: 99,
        },
      ],
    });

    await act(async () => {
      root.render(<GcLapsePanel />);
    });
    await flush();

    expect(container.querySelector('[data-testid="gc-lapse-panel"]')).not.toBeNull();
    const btn = container.querySelector(
      '[data-testid="gc-lapse-relocate-p-1"]'
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    await flush();

    expect(emergencyRelocate).toHaveBeenCalledWith('planet', 'p-1');
    expect(refreshPlayerState).toHaveBeenCalled();
  });

  it('surfaces relocate 400 server detail in feedback', async () => {
    getStatus.mockResolvedValue({
      lapsed: true, gc_lapsed_at: '2026-08-01T00:00:00Z', relocation_available: true,
      foreign_holdings: [{ asset_type: 'planet', asset_id: 'p-1', name: 'Outpost', region_id: 'r-2', sector_id: 99 }],
    });
    emergencyRelocate.mockRejectedValue(Object.assign(new Error('Emergency relocation already used'), { status: 400 }));
    await act(async () => { root.render(<GcLapsePanel />); });
    await flush();
    const btn = container.querySelector('[data-testid="gc-lapse-relocate-p-1"]') as HTMLButtonElement;
    await act(async () => { btn.click(); });
    await flush();
    expect(container.querySelector('[data-testid="gc-lapse-feedback"]')?.textContent).toBe('Emergency relocation already used');
  });

  it('formatGcLapseRelocateError falls back when detail absent', () => {
    expect(formatGcLapseRelocateError(new Error('API Error: 400'))).toBe('Relocation failed');
  });
});
