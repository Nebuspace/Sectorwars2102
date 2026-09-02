import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AuditLogViewer } from './AuditLogViewer';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

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
 * LEG-3440 Soft-ORDER — AuditLogViewer TypeError/Network Error honesty densify.
 */
describe('AuditLogViewer typeErrorHonesty densify (LEG-3440)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error to gameserver-unreachable audit fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable — network error fetching audit logs/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toContain('not implemented');
  });

  it('collapses TypeError Failed to fetch to gameserver-unreachable audit fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable — network error fetching audit logs/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toContain('not implemented');
  });

  it('surfaces 403 with AUDIT_VIEW scope hint when audit log GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/AUDIT_VIEW|Access denied/i);
    expect(alert).toMatch(/audit log/i);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on audit log load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });
});
