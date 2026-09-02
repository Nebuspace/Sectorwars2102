import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import AddRegionDialog from './AddRegionDialog';
import { formatAdminApiError } from '../../../utils/adminApiError';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const FALLBACK = 'Failed to add player-owned region';
const formatOptions = {
  fallback: FALLBACK,
  scopeHint: 'admin.universe.manage',
} as const;

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: {} },
  });

/**
 * LEG-3811 Soft-ORDER — AddRegionDialog parent-supplied error TypeError/Network densify.
 * LEG-3943 Soft-ORDER — HTTP 403/429 densify via formatAdminApiError.
 */
describe('AddRegionDialog typeErrorHonesty densify (LEG-3811)', () => {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();

  beforeEach(() => {
    onCancel.mockReset();
    onConfirm.mockReset();
    onConfirm.mockResolvedValue(undefined);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces parent-collapsed TypeError Failed to fetch without leaking transport text', () => {
    const collapsed = formatAdminApiError(
      new TypeError('Failed to fetch'),
      formatOptions,
    );

    render(
      <AddRegionDialog
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/Failed to add player-owned region/i);
    assertNoTransportLeak(text);
  });

  it('surfaces parent-collapsed Network Error without leaking raw transport text', () => {
    const collapsed = formatAdminApiError(new Error('Network Error'), formatOptions);

    render(
      <AddRegionDialog
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/Failed to add player-owned region/i);
    assertNoTransportLeak(text);
  });

  it('surfaces parent-collapsed Failed to fetch without leaking transport text', () => {
    const collapsed = formatAdminApiError(new Error('Failed to fetch'), formatOptions);

    render(
      <AddRegionDialog
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/Failed to add player-owned region/i);
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail from parent error prop', () => {
    render(
      <AddRegionDialog
        onCancel={onCancel}
        onConfirm={onConfirm}
        error="Region seed already in use"
      />,
    );

    expect(screen.getByText('Region seed already in use')).toBeTruthy();
  });

  it('parent catch on onConfirm rejection collapses transport errors in DOM', async () => {
    onConfirm.mockRejectedValue(new TypeError('Failed to fetch'));

    function ParentHarness() {
      const [error, setError] = useState<string | null>(null);
      return (
        <AddRegionDialog
          onCancel={onCancel}
          onConfirm={async (seed, sectors) => {
            try {
              await onConfirm(seed, sectors);
            } catch (err) {
              setError(formatAdminApiError(err, formatOptions));
            }
          }}
          error={error}
        />
      );
    }

    render(<ParentHarness />);

    const form = screen.getByLabelText('bang.addRegion.sectors').closest('form');
    expect(form).toBeTruthy();
    form!.noValidate = true;
    fireEvent.submit(form!);

    await waitFor(() => {
      expect(screen.getByText(/Failed to add player-owned region/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to add player-owned region/i).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('surfaces parent-collapsed 403 with admin.universe.manage without transport leak', () => {
    const collapsed = formatAdminApiError(axiosError(403), formatOptions);

    render(
      <AddRegionDialog
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/Access denied/i);
    expect(text).toMatch(/admin\.universe\.manage/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces parent-collapsed 429 rate-limit copy without transport leak', () => {
    const collapsed = formatAdminApiError(axiosError(429), formatOptions);

    render(
      <AddRegionDialog
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

  it('parent catch on onConfirm 403 collapses to scope copy without transport leak', async () => {
    onConfirm.mockRejectedValue(axiosError(403));

    function ParentHarness() {
      const [error, setError] = useState<string | null>(null);
      return (
        <AddRegionDialog
          onCancel={onCancel}
          onConfirm={async (seed, sectors) => {
            try {
              await onConfirm(seed, sectors);
            } catch (err) {
              setError(formatAdminApiError(err, formatOptions));
            }
          }}
          error={error}
        />
      );
    }

    render(<ParentHarness />);

    const form = screen.getByLabelText('bang.addRegion.sectors').closest('form');
    expect(form).toBeTruthy();
    form!.noValidate = true;
    fireEvent.submit(form!);

    await waitFor(() => {
      expect(screen.getByText(/Access denied/i)).toBeTruthy();
    });

    const text = screen.getByText(/Access denied/i).textContent ?? '';
    expect(text).toMatch(/admin\.universe\.manage/i);
    expect(text).not.toMatch(/\b403\b/);
    assertNoTransportLeak(text);
  });
});
