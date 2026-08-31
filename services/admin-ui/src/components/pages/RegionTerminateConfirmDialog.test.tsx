import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import RegionTerminateConfirmDialog, {
  formatRegionTerminateError,
} from './RegionTerminateConfirmDialog';
import * as regionTerminateApi from '../../services/regionTerminateApi';

vi.mock('../../services/regionTerminateApi', () => ({
  fetchRegionTerminatePreview: vi.fn(),
  postRegionTerminate: vi.fn(),
}));

const preview = {
  regionId: 'reg-1',
  regionName: 'sol-reach',
  displayName: 'Sol Reach',
  status: 'active',
  regionType: 'player_owned',
  planetCount: 3,
  stationCount: 2,
  sectorCount: 12,
  playerStakeholderCount: 1,
  terminable: true,
};

describe('RegionTerminateConfirmDialog (LEG-3206)', () => {
  beforeEach(() => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockResolvedValue(preview);
  });

  it('loads preview and shows dependent-entity counts', async () => {
    render(
      <RegionTerminateConfirmDialog
        regionId="reg-1"
        onCancel={() => {}}
        onConfirm={async () => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/Planets: 3/)).toBeTruthy();
    });
    expect(regionTerminateApi.fetchRegionTerminatePreview).toHaveBeenCalledWith('reg-1');
  });

  it('surfaces scope denial on preview 403', async () => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: { detail: 'Missing admin.regions.terminate' } },
      }),
    );

    render(
      <RegionTerminateConfirmDialog
        regionId="reg-1"
        onCancel={() => {}}
        onConfirm={async () => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Missing admin\.regions\.terminate/);
    });
  });

  it('calls onConfirm with typed name and reason when fully confirmed', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);

    render(
      <RegionTerminateConfirmDialog
        regionId="reg-1"
        onCancel={() => {}}
        onConfirm={onConfirm}
      />,
    );

    await waitFor(() => {
      expect(screen.getByLabelText(/Type the region name/i)).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText(/Type the region name/i), {
      target: { value: 'sol-reach' },
    });
    fireEvent.change(screen.getByLabelText(/Reason \(required/i), {
      target: { value: 'policy violation' },
    });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Terminate Region' }));

    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalledWith('sol-reach', 'policy violation');
    });
  });

  it('formatRegionTerminateError falls back on TypeError network collapse', () => {
    expect(formatRegionTerminateError(new TypeError('Failed to fetch'))).toBe(
      'Network error — could not reach the gameserver. Check your connection and try again.',
    );
  });
});
