import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SystemHealthStatus from './SystemHealthStatus';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

describe('SystemHealthStatus (LEG-212 shared api)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('shows offline for the game server when the status call fails', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('network'));

    render(<SystemHealthStatus />);

    await waitFor(() => expect(screen.getByText(/Offline/)).toBeInTheDocument());
  });

  it('shows online + connection counts when status calls succeed', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/status/database')) {
        return {
          data: {
            status: 'healthy',
            connected: true,
            response_time: 5,
            last_check: '2026-01-01T00:00:00Z',
          },
        };
      }
      if (String(url).includes('/status/ai/providers')) {
        return {
          data: {
            status: 'healthy',
            providers: {},
            summary: { healthy: 2, configured: 2, total: 2 },
            response_time: 3,
            last_check: '2026-01-01T00:00:00Z',
          },
        };
      }
      return {
        data: { active_connections: 4, admin_connections: 1 },
      };
    });

    render(<SystemHealthStatus />);

    await waitFor(() => expect(screen.getByText(/Online/)).toBeInTheDocument());
    expect(screen.getByText(/Connected/)).toBeInTheDocument();
    expect(vi.mocked(api.get).mock.calls.map(([u]) => String(u))).toEqual(
      expect.arrayContaining([
        '/api/v1/status/',
        '/api/v1/status/ai/providers',
        '/api/v1/status/database/detailed',
      ])
    );
  });

  it('expands to show detailed metrics when the header is clicked', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/status/ai/providers')) {
        return {
          data: {
            status: 'healthy',
            providers: {},
            summary: { healthy: 0, configured: 0, total: 0 },
            response_time: 0,
            last_check: '2026-01-01T00:00:00Z',
          },
        };
      }
      if (String(url).includes('/status/database')) {
        return {
          data: {
            status: 'healthy',
            connected: true,
            response_time: 0,
            last_check: '2026-01-01T00:00:00Z',
          },
        };
      }
      return { data: {} };
    });
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
