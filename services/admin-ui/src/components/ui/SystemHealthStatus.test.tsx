import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SystemHealthStatus from './SystemHealthStatus';

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
  } as Response);
}

describe('SystemHealthStatus', () => {
  beforeEach(() => {
    localStorage.setItem('accessToken', 'tok');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('shows offline for the game server when the status fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.includes('/status/')) return jsonResponse({}, false);
      return jsonResponse({});
    }));

    render(<SystemHealthStatus />);

    await waitFor(() => expect(screen.getByText(/Offline/)).toBeInTheDocument());
  });

  it('shows online + connection counts when the server status fetch succeeds', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.includes('/status/database')) {
        return jsonResponse({ status: 'healthy', connected: true, response_time: 5, last_check: '2026-01-01T00:00:00Z' });
      }
      if (url.includes('/status/ai/providers')) {
        return jsonResponse({
          status: 'healthy',
          providers: {},
          summary: { healthy: 2, configured: 2, total: 2 },
          response_time: 3,
          last_check: '2026-01-01T00:00:00Z',
        });
      }
      return jsonResponse({ active_connections: 4, admin_connections: 1 });
    }));

    render(<SystemHealthStatus />);

    await waitFor(() => expect(screen.getByText(/Online \(5\)/)).toBeInTheDocument());
    expect(screen.getByText(/Connected/)).toBeInTheDocument();
  });

  it('expands to show detailed metrics when the header is clicked', async () => {
    // Give the AI-providers and database-health endpoints well-shaped
    // bodies: the component reads `aiHealth.summary.healthy` and
    // `dbHealth.response_time.toFixed(...)` without null-guarding those
    // nested fields (pre-existing; out of scope to fix here), so an empty
    // `{}` body throws once the detail panel renders them.
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      if (url.includes('/status/ai/providers')) {
        return jsonResponse({
          status: 'healthy',
          providers: {},
          summary: { healthy: 0, configured: 0, total: 0 },
          response_time: 0,
          last_check: '2026-01-01T00:00:00Z',
        });
      }
      if (url.includes('/status/database')) {
        return jsonResponse({
          status: 'healthy',
          connected: true,
          response_time: 0,
          last_check: '2026-01-01T00:00:00Z',
        });
      }
      return jsonResponse({});
    }));
    const user = userEvent.setup();

    render(<SystemHealthStatus />);
    await waitFor(() =>
      expect(screen.getByTitle('Click to expand/collapse system details')).toBeInTheDocument()
    );

    expect(screen.queryByText('Player Connections:')).not.toBeInTheDocument();
    await user.click(screen.getByTitle('Click to expand/collapse system details'));
    expect(screen.getByText('Player Connections:')).toBeInTheDocument();
  });
});
