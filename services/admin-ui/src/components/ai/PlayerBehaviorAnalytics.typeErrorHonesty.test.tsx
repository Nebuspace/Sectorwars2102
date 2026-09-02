import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { PlayerBehaviorAnalytics } from './PlayerBehaviorAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useAIUpdates: () => undefined,
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
 * LEG-3746 Soft-ORDER — PlayerBehaviorAnalytics TypeError/network + HTTP honesty densify.
 */
describe('PlayerBehaviorAnalytics typeErrorHonesty densify (LEG-3746)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load behavior analytics/i);
    assertNoTransportLeak(alert);
  });

  it('collapses TypeError Failed to fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load behavior analytics/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 401 as authentication-required copy', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(401));

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Authentication required/i);
    expect(alert).toMatch(/log in as an admin user/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 403 with PLAYERS_VIEW scope hint when no server detail', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW/i);
    expect(alert).toMatch(/behavior analytics/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces server detail on 403 when provided', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.players.view (PLAYERS_VIEW)'),
    );

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(
      /Missing scope admin\.players\.view \(PLAYERS_VIEW\)/i,
    );
  });

  it('surfaces 429 as admin rate-limit copy', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    assertNoTransportLeak(alert);
  });
});
