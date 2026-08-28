// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockPreview = vi.fn();
vi.mock('../../../services/api', () => ({
  miningAPI: {
    getYieldPreview: (...a: unknown[]) => mockPreview(...a),
  },
}));

import HarvestYieldPreview from '../HarvestYieldPreview';

describe('HarvestYieldPreview', () => {
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

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders ore band, laser, richness, and turns from tip payload', async () => {
    mockPreview.mockResolvedValue({
      success: true,
      reason: null,
      ore_lo: 8,
      ore_hi: 12,
      richness_tier: 3,
      laser_level: 2,
      depletion_modifier: 1,
      turns_cost: 5,
    });
    await act(async () => {
      root.render(<HarvestYieldPreview shipId="ship-9" />);
    });
    await flush();
    expect(mockPreview).toHaveBeenCalledWith('ship-9');
    const band = container.querySelector('[data-testid="harvest-yield-band"]');
    expect(band?.textContent).toContain('Expected ore 8–12');
    expect(band?.textContent).toContain('L2 laser');
    expect(band?.textContent).toContain('tier 3');
    expect(band?.textContent).toContain('5 turns');
  });

  it('surfaces mocked 400 no_mining_laser via HARVEST_GATE_COPY', async () => {
    mockPreview.mockRejectedValue(new Error('no_mining_laser'));
    await act(async () => {
      root.render(<HarvestYieldPreview shipId="ship-9" />);
    });
    await flush();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'No mining laser equipped',
    );
  });

  it('does not call GET when shipId is missing', async () => {
    const onGateChange = vi.fn();
    await act(async () => {
      root.render(<HarvestYieldPreview shipId={undefined} onGateChange={onGateChange} />);
    });
    await flush();
    expect(mockPreview).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="harvest-yield-preview"]')?.textContent)
      .toContain('No active ship to preview yield.');
    expect(onGateChange).toHaveBeenCalledWith({
      blocked: true,
      message: 'No active ship to preview yield.',
      reasonKey: null,
    });
  });

  it('notifies onGateChange when preview succeeds', async () => {
    const onGateChange = vi.fn();
    mockPreview.mockResolvedValue({
      success: true,
      reason: null,
      ore_lo: 8,
      ore_hi: 12,
    });
    await act(async () => {
      root.render(<HarvestYieldPreview shipId="ship-9" onGateChange={onGateChange} />);
    });
    await flush();
    expect(onGateChange).toHaveBeenLastCalledWith({
      blocked: false,
      message: null,
      reasonKey: null,
    });
  });

  it('notifies onGateChange when preview rejects no_mining_laser', async () => {
    const onGateChange = vi.fn();
    mockPreview.mockRejectedValue(new Error('no_mining_laser'));
    await act(async () => {
      root.render(<HarvestYieldPreview shipId="ship-9" onGateChange={onGateChange} />);
    });
    await flush();
    expect(onGateChange).toHaveBeenLastCalledWith({
      blocked: true,
      message: 'No mining laser equipped — fit one at a TradeDock to extract ore.',
      reasonKey: 'no_mining_laser',
    });
  });
});
