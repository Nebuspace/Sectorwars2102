import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import RegionTerminateConfirmDialog from './RegionTerminateConfirmDialog';
import * as regionTerminateApi from '../../services/regionTerminateApi';

vi.mock('../../services/regionTerminateApi', () => ({
  fetchRegionTerminatePreview: vi.fn(),
  postRegionTerminate: vi.fn(),
}));

const HONEST =
  'Network error — could not reach the gameserver. Check your connection and try again.';

/**
 * LEG-3484 Soft-ORDER — RegionTerminateConfirmDialog TypeError/Network Error honesty densify.
 */
describe('RegionTerminateConfirmDialog typeErrorHonesty densify (LEG-3484)', () => {
  beforeEach(() => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on terminate preview to gameserver-unreachable alert', async () => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockRejectedValue(
      new Error('Network Error'),
    );

    render(
      <RegionTerminateConfirmDialog
        regionId="reg-1"
        onCancel={() => {}}
        onConfirm={async () => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toBe(HONEST);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on terminate preview to gameserver-unreachable alert', async () => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockRejectedValue(
      new TypeError('Failed to fetch'),
    );

    render(
      <RegionTerminateConfirmDialog
        regionId="reg-1"
        onCancel={() => {}}
        onConfirm={async () => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toBe(HONEST);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
  });
});
