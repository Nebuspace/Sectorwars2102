import { describe, expect, it } from 'vitest';
import {
  miningHarvestNotificationToast,
  miningLicenseExpiryWarningToast,
} from '../miningWsNotifications';

describe('miningWsNotifications', () => {
  it('maps harvest_success payload to a success toast', () => {
    const toast = miningHarvestNotificationToast({
      type: 'mining_harvest_notification',
      subtype: 'harvest_success',
      delivery: ['inbox', 'toast'],
      payload: { ore: 12, precious_metals: 0, quantum_shards: 0 },
    });
    expect(toast).toEqual({
      title: 'Harvest Complete',
      content: 'Extracted 12 ore.',
      level: 'success',
    });
  });

  it('maps precious_metals subtype separately', () => {
    const toast = miningHarvestNotificationToast({
      subtype: 'precious_metals',
      delivery: ['toast'],
      payload: { amount: 2 },
    });
    expect(toast?.title).toBe('Rare Drop — Precious Metals');
    expect(toast?.content).toContain('2 precious metals');
  });

  it('maps quantum_shards subtype separately', () => {
    const toast = miningHarvestNotificationToast({
      subtype: 'quantum_shards',
      delivery: ['toast'],
      payload: { amount: 1 },
    });
    expect(toast?.title).toBe('Trace Drop — Quantum Shards');
    expect(toast?.content).toContain('1 quantum shard');
  });

  it('skips toast when delivery excludes toast', () => {
    expect(
      miningHarvestNotificationToast({
        subtype: 'harvest_success',
        delivery: ['inbox'],
        payload: { ore: 1 },
      }),
    ).toBeNull();
  });

  it('maps license expiry warning with sector + renew copy', () => {
    const toast = miningLicenseExpiryWarningToast({
      type: 'mining_license_expiry_warning',
      delivery: ['inbox', 'toast'],
      payload: {
        license_id: 'lic-1',
        sector_number: 42,
        expires_at: '2026-08-28T13:00:00.000Z',
      },
    });
    expect(toast?.title).toBe('Mining License Expiring');
    expect(toast?.content).toContain('sector 42');
    expect(toast?.content).toContain('Astral Mining');
    expect(toast?.level).toBe('warning');
  });
});
