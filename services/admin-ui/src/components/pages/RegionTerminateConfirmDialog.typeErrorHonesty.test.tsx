import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import RegionTerminateConfirmDialog, {
  formatRegionTerminateError,
} from './RegionTerminateConfirmDialog';
import * as regionTerminateApi from '../../services/regionTerminateApi';

vi.mock('../../services/regionTerminateApi', () => ({
  fetchRegionTerminatePreview: vi.fn(),
  postRegionTerminate: vi.fn(),
}));

const HONEST =
  'Network error — could not reach the gameserver. Check your connection and try again.';

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3484 Soft-ORDER — RegionTerminateConfirmDialog TypeError/Network Error honesty densify.
 * LEG-3916 Soft-ORDER — HTTP 429 densify (invent=0).
 * LEG-4105 Soft-ORDER — HTTP 403 densify (invent=0).
 */
describe('RegionTerminateConfirmDialog typeErrorHonesty densify (LEG-3484 / LEG-3916 / LEG-4105)', () => {
  beforeEach(() => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('formatRegionTerminateError surfaces 403 as admin.regions.terminate scope copy', () => {
    const collapsed = formatRegionTerminateError(axiosError(403));
    expect(collapsed).toMatch(/access denied/i);
    expect(collapsed).toMatch(/admin\.regions\.terminate/i);
    expect(collapsed).not.toMatch(/\b403\b/);
    expect(collapsed).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(collapsed);
  });

  it('formatRegionTerminateError surfaces 429 as admin rate-limit copy', () => {
    const collapsed = formatRegionTerminateError(axiosError(429));
    expect(collapsed).toMatch(/rate limit/i);
    expect(collapsed).not.toMatch(/\b429\b/);
    expect(collapsed).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(collapsed);
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

  it('surfaces 403 as admin.regions.terminate scope copy on terminate preview', async () => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockRejectedValue(
      axiosError(403),
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
    expect(alert).toMatch(/access denied/i);
    expect(alert).toMatch(/admin\.regions\.terminate/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on terminate preview', async () => {
    vi.mocked(regionTerminateApi.fetchRegionTerminatePreview).mockRejectedValue(
      axiosError(429),
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
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });
});
