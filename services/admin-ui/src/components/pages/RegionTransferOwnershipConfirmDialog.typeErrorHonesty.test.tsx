import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import RegionTransferOwnershipConfirmDialog, {
  formatRegionTransferError,
} from './RegionTransferOwnershipConfirmDialog';

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

const VALID_OWNER = 'a1b2c3d4-e5f6-4789-a012-3456789abcde';

/**
 * LEG-3967 — RegionTransferOwnershipConfirmDialog TypeError/Network Error/429 honesty.
 */
describe('RegionTransferOwnershipConfirmDialog typeErrorHonesty (LEG-3967)', () => {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();

  beforeEach(() => {
    onCancel.mockReset();
    onConfirm.mockReset();
    onConfirm.mockResolvedValue(undefined);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('formatRegionTransferError surfaces 429 as admin rate-limit copy', () => {
    const collapsed = formatRegionTransferError(axiosError(429));
    expect(collapsed).toMatch(/rate limit/i);
    expect(collapsed).not.toMatch(/\b429\b/);
    expect(collapsed).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(collapsed);
  });

  it('formatRegionTransferError collapses TypeError Failed to fetch', () => {
    const collapsed = formatRegionTransferError(new TypeError('Failed to fetch'));
    expect(collapsed).toBe(HONEST);
    assertNoTransportLeak(collapsed);
  });

  it('formatRegionTransferError collapses axios Network Error', () => {
    const collapsed = formatRegionTransferError(new Error('Network Error'));
    expect(collapsed).toBe(HONEST);
    assertNoTransportLeak(collapsed);
  });

  it('surfaces parent-collapsed transport error via error prop without leaking raw text', () => {
    const collapsed = formatRegionTransferError(new TypeError('Failed to fetch'));

    render(
      <RegionTransferOwnershipConfirmDialog
        regionId="reg-1"
        regionDisplayName="Sol Reach"
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toBe(HONEST);
    assertNoTransportLeak(alert.textContent ?? '');
  });

  it('parent catch on onConfirm rejection collapses transport errors in DOM', async () => {
    onConfirm.mockRejectedValue(new Error('Network Error'));

    function ParentHarness() {
      const [error, setError] = useState<string | null>(null);
      return (
        <RegionTransferOwnershipConfirmDialog
          regionId="reg-1"
          regionDisplayName="Sol Reach"
          onCancel={onCancel}
          onConfirm={async (ownerId, reason) => {
            try {
              await onConfirm(ownerId, reason);
            } catch (err) {
              setError(formatRegionTransferError(err));
            }
          }}
          error={error}
        />
      );
    }

    render(<ParentHarness />);

    fireEvent.change(screen.getByLabelText(/New owner user UUID/i), {
      target: { value: VALID_OWNER },
    });
    fireEvent.change(screen.getByLabelText(/Reason \(required/i), {
      target: { value: 'account recovery' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Transfer Ownership' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toBe(HONEST);
    assertNoTransportLeak(alert);
  });

  it('parent catch on onConfirm rejection surfaces 429 as rate-limit copy', async () => {
    onConfirm.mockRejectedValue(axiosError(429));

    function ParentHarness() {
      const [error, setError] = useState<string | null>(null);
      return (
        <RegionTransferOwnershipConfirmDialog
          regionId="reg-1"
          regionDisplayName="Sol Reach"
          onCancel={onCancel}
          onConfirm={async (ownerId, reason) => {
            try {
              await onConfirm(ownerId, reason);
            } catch (err) {
              setError(formatRegionTransferError(err));
            }
          }}
          error={error}
        />
      );
    }

    render(<ParentHarness />);

    fireEvent.change(screen.getByLabelText(/New owner user UUID/i), {
      target: { value: VALID_OWNER },
    });
    fireEvent.change(screen.getByLabelText(/Reason \(required/i), {
      target: { value: 'ops handoff' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Transfer Ownership' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    assertNoTransportLeak(alert);
  });
});
