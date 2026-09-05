import React, { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import WipeGalaxyConfirmDialog from './WipeGalaxyConfirmDialog';
import { formatAdminApiError } from '../../../utils/adminApiError';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const FALLBACK = 'Could not wipe galaxy: request failed';
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
 * LEG-3814 Soft-ORDER — WipeGalaxyConfirmDialog parent-supplied error TypeError/Network densify.
 * LEG-3943 Soft-ORDER — HTTP 403/429 densify via formatAdminApiError.
 */
describe('WipeGalaxyConfirmDialog typeErrorHonesty densify (LEG-3814)', () => {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();
  const galaxyName = 'Andromeda';

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
      <WipeGalaxyConfirmDialog
        galaxyName={galaxyName}
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/Could not wipe galaxy: request failed/i);
    assertNoTransportLeak(text);
  });

  it('surfaces parent-collapsed Network Error without leaking raw transport text', () => {
    const collapsed = formatAdminApiError(new Error('Network Error'), formatOptions);

    render(
      <WipeGalaxyConfirmDialog
        galaxyName={galaxyName}
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/Could not wipe galaxy: request failed/i);
    assertNoTransportLeak(text);
  });

  it('surfaces parent-collapsed Failed to fetch without leaking transport text', () => {
    const collapsed = formatAdminApiError(new Error('Failed to fetch'), formatOptions);

    render(
      <WipeGalaxyConfirmDialog
        galaxyName={galaxyName}
        onCancel={onCancel}
        onConfirm={onConfirm}
        error={collapsed}
      />,
    );

    const text = screen.getByText(collapsed).textContent ?? '';
    expect(text).toMatch(/Could not wipe galaxy: request failed/i);
    assertNoTransportLeak(text);
  });

  it('preserves non-transport server detail from parent error prop', () => {
    render(
      <WipeGalaxyConfirmDialog
        galaxyName={galaxyName}
        onCancel={onCancel}
        onConfirm={onConfirm}
        error="Galaxy wipe rejected by gameserver"
      />,
    );

    expect(screen.getByText('Galaxy wipe rejected by gameserver')).toBeTruthy();
  });

  it('parent catch on onConfirm rejection collapses transport errors in DOM', async () => {
    onConfirm.mockRejectedValue(new TypeError('Failed to fetch'));

    function ParentHarness() {
      const [error, setError] = useState<string | null>(null);
      return (
        <WipeGalaxyConfirmDialog
          galaxyName={galaxyName}
          onCancel={onCancel}
          onConfirm={async (confirmName) => {
            try {
              await onConfirm(confirmName);
            } catch (err) {
              setError(formatAdminApiError(err, formatOptions));
            }
          }}
          error={error}
        />
      );
    }

    render(<ParentHarness />);

    fireEvent.change(screen.getByLabelText('bang.wipe.prompt'), {
      target: { value: galaxyName },
    });

    const form = screen.getByLabelText('bang.wipe.prompt').closest('form');
    expect(form).toBeTruthy();
    fireEvent.submit(form!);

    await waitFor(() => {
      expect(screen.getByText(/Could not wipe galaxy: request failed/i)).toBeTruthy();
    });

    const text = screen.getByText(/Could not wipe galaxy: request failed/i).textContent ?? '';
    assertNoTransportLeak(text);
  });

  it('surfaces parent-collapsed 403 with admin.universe.manage without transport leak', () => {
    const collapsed = formatAdminApiError(axiosError(403), formatOptions);

    render(
      <WipeGalaxyConfirmDialog
        galaxyName={galaxyName}
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
      <WipeGalaxyConfirmDialog
        galaxyName={galaxyName}
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
});
