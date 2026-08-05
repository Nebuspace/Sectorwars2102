// @vitest-environment jsdom
/**
 * MyBeaconsTab — the StatusBar dossier "BEACONS" tab. Mirrors
 * GovSummaryTab.test.tsx's seam (jsdom + react-dom/client createRoot +
 * act(), no RTL in this project).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockMine = vi.fn();
const mockRead = vi.fn();
const mockSalvage = vi.fn();
const mockRecharge = vi.fn();
const mockReport = vi.fn();

vi.mock('../../services/api', () => ({
  beaconAPI: {
    mine: (...a: unknown[]) => mockMine(...a),
    read: (...a: unknown[]) => mockRead(...a),
    salvage: (...a: unknown[]) => mockSalvage(...a),
    recharge: (...a: unknown[]) => mockRecharge(...a),
    report: (...a: unknown[]) => mockReport(...a),
  },
}));

import MyBeaconsTab from './MyBeaconsTab';

const BEACON_A = {
  id: 'beacon-1',
  sector_id: 42,
  preview: 'Hello, traveler',
  deployed_at: '2026-07-01T00:00:00Z',
  charge_expires_at: '2026-08-01T00:00:00Z',
  expiry: '2026-08-15T00:00:00Z',
  state: 'active',
  read_once: false,
  read_count: 3,
  flagged: false,
};

describe('MyBeaconsTab', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockMine.mockReset();
    mockRead.mockReset();
    mockSalvage.mockReset();
    mockRecharge.mockReset();
    mockReport.mockReset();
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

  const mount = async () => {
    await act(async () => {
      root.render(<MyBeaconsTab />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('shows the empty state with zero beacons', async () => {
    mockMine.mockResolvedValue({ beacons: [] });
    await mount();

    expect(container.textContent).toContain('No Beacons Deployed');
  });

  it('renders a deployed beacon row with sector, preview, and read count', async () => {
    mockMine.mockResolvedValue({ beacons: [BEACON_A] });
    await mount();

    expect(container.textContent).toContain('SECTOR 42');
    expect(container.textContent).toContain('Hello, traveler');
    expect(container.textContent).toContain('Reads: 3');
  });

  it('shows an error state instead of crashing when the fetch fails', async () => {
    mockMine.mockRejectedValue(new Error('Network down'));
    await mount();

    const errorEl = container.querySelector('.sb-beacons-error');
    expect(errorEl?.textContent).toBe('Failed to load your beacons');
    expect(errorEl?.getAttribute('role')).toBe('alert');
  });

  it('Read wires to beaconAPI.read and expands the full message', async () => {
    mockMine.mockResolvedValue({ beacons: [BEACON_A] });
    mockRead.mockResolvedValue({ id: 'beacon-1', message: 'The full secret message.', read_count: 4 });
    await mount();

    const readBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Read')!;
    await act(async () => {
      readBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockRead).toHaveBeenCalledWith('beacon-1');
    expect(container.textContent).toContain('The full secret message.');
    expect(container.textContent).toContain('Reads: 4');
  });

  it('Salvage wires to beaconAPI.salvage and removes the row', async () => {
    mockMine.mockResolvedValue({ beacons: [BEACON_A] });
    mockSalvage.mockResolvedValue({ id: 'beacon-1', salvage_refund: 250, credits: 750, turns: 9 });
    await mount();

    const btn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.startsWith('Salvage'))!;
    await act(async () => {
      btn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockSalvage).toHaveBeenCalledWith('beacon-1');
    expect(container.textContent).toContain('No Beacons Deployed');
  });

  it('Recharge wires to beaconAPI.recharge and updates the row in place', async () => {
    mockMine.mockResolvedValue({ beacons: [BEACON_A] });
    mockRecharge.mockResolvedValue({
      id: 'beacon-1', recharge_cost: 200, credits: 550,
      charge_expires_at: '2026-09-01T00:00:00Z', expiry: '2026-09-15T00:00:00Z', state: 'active',
    });
    await mount();

    const btn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.startsWith('Recharge'))!;
    await act(async () => {
      btn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockRecharge).toHaveBeenCalledWith('beacon-1');
    expect(container.textContent).toContain('SECTOR 42');
    expect(container.textContent).toContain('Recharged.');
  });

  it('Report wires to beaconAPI.report and removes the row', async () => {
    mockMine.mockResolvedValue({ beacons: [BEACON_A] });
    mockReport.mockResolvedValue({ id: 'beacon-1', flagged: true, already_flagged: false });
    await mount();

    const btn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Report')!;
    await act(async () => {
      btn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockReport).toHaveBeenCalledWith('beacon-1');
    expect(container.textContent).toContain('No Beacons Deployed');
  });

  it('surfaces a location hint (not a raw error) on a same-sector-gated 404', async () => {
    mockMine.mockResolvedValue({ beacons: [BEACON_A] });
    mockRead.mockRejectedValue(new Error('Beacon beacon-1 not found'));
    await mount();

    const readBtn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Read')!;
    await act(async () => {
      readBtn.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Not in sector 42 right now — travel there to read.');
  });
});
