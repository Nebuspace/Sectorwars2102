// @vitest-environment jsdom
/**
 * StationSecurityMonitoringPane — LEG-3106 owner security tier controls.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getSecurityStatus = vi.fn();
const upgradeSecurity = vi.fn();
const downgradeSecurity = vi.fn();

vi.mock('../../../services/api', () => ({
  stationSecurityAPI: {
    getSecurityStatus: (...args: unknown[]) => getSecurityStatus(...args),
    upgradeSecurity: (...args: unknown[]) => upgradeSecurity(...args),
    downgradeSecurity: (...args: unknown[]) => downgradeSecurity(...args),
  },
}));

import StationSecurityMonitoringPane, {
  formatSecurityTierLabel,
} from '../StationSecurityMonitoringPane';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatSecurityTierLabel', () => {
  it('title-cases tiers', () => {
    expect(formatSecurityTierLabel('standard')).toBe('Standard');
    expect(formatSecurityTierLabel('none')).toBe('None');
  });
});

describe('StationSecurityMonitoringPane', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    getSecurityStatus.mockReset();
    upgradeSecurity.mockReset();
    downgradeSecurity.mockReset();
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

  it('renders nothing for non-owners', async () => {
    await act(async () => {
      root.render(<StationSecurityMonitoringPane stationId="st-1" isOwner={false} />);
    });
    await flush();
    expect(container.querySelector('[data-testid="po-security-monitoring"]')).toBeNull();
    expect(getSecurityStatus).not.toHaveBeenCalled();
  });

  it('shows tier and upgrades for owner', async () => {
    getSecurityStatus.mockResolvedValue({
      station_id: 'st-1',
      tier: 'basic',
      upkeep_collected: 0,
    });
    upgradeSecurity.mockResolvedValue({
      message: 'Upgrade initiated',
      station_id: 'st-1',
      current_tier: 'basic',
      upgrade_to: 'standard',
      cost: 200_000,
      completes_at: new Date(Date.now() + 3600_000).toISOString(),
    });
    await act(async () => {
      root.render(<StationSecurityMonitoringPane stationId="st-1" isOwner={true} />);
    });
    await act(async () => {
      await flush();
    });
    expect(getSecurityStatus).toHaveBeenCalledWith('st-1');
    expect(container.querySelector('[data-testid="po-security-tier"]')?.textContent).toMatch(/Basic/);
    const upgradeBtn = container.querySelector('[data-testid="po-security-upgrade"]') as HTMLButtonElement;
    expect(upgradeBtn.disabled).toBe(false);
    await act(async () => {
      upgradeBtn.click();
      await flush();
    });
    expect(upgradeSecurity).toHaveBeenCalledWith('st-1');
    expect(container.querySelector('[data-testid="po-security-action-msg"]')?.textContent).toMatch(
      /Upgrade initiated/,
    );
  });

  it('surfaces load errors', async () => {
    getSecurityStatus.mockRejectedValue(new Error('Station not found'));
    await act(async () => {
      root.render(<StationSecurityMonitoringPane stationId="st-missing" isOwner={true} />);
    });
    await act(async () => {
      await flush();
    });
    expect(container.textContent).toMatch(/Station not found/);
  });
});
