import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AuditLogViewer } from './AuditLogViewer';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const samplePayload = {
  logs: [
    {
      id: 'log-1',
      timestamp: '2026-08-16T12:00:00Z',
      userId: 'u1',
      username: 'ops-admin',
      action: 'login',
      resource: 'session',
      details: {},
      ipAddress: '127.0.0.1',
      userAgent: 'vitest',
      status: 'success' as const,
      duration: 12,
    },
  ],
  pages: 2,
  total: 1,
  page: 1,
  limit: 50,
};

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('AuditLogViewer (LEG-174)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loads audit logs via shared api and hydrates without not-implemented copy', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: samplePayload });

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByText('ops-admin')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/audit/logs', {
      params: expect.objectContaining({
        page: '1',
        limit: '50',
        sortField: 'timestamp',
        sortOrder: 'desc',
      }),
    });
    expect(screen.getByText('Legacy HTTP Audit Trail')).toBeTruthy();
    expect(screen.getByText(/Page 1 of 2/)).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('reports a 403 as a scope problem, never as unimplemented', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/admin\.audit\.view|AUDIT_VIEW|Access denied/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports a 429 as admin rate-limit, never as gameserver-down', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/unreachable|gameserver/i);
  });

  it('reports a 404 as a routing fault, never as an unbuilt endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toMatch(/route not found|proxy/i);
    expect(alert).not.toContain('not implemented');
  });

  it('reports network errors without inventing an unimplemented endpoint', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<AuditLogViewer />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/network error|unreachable/i);
    expect(alert).not.toContain('not implemented');
  });
});
