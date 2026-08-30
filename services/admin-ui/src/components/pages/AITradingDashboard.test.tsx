import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import AITradingDashboard from './AITradingDashboard';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    isConnected: false,
    subscribe: () => () => {},
  }),
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { is_admin: true }, token: 'tok' }),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../ai/MarketPredictionInterface', () => ({ MarketPredictionInterface: () => null }));
vi.mock('../ai/RouteOptimizationDisplay', () => ({ RouteOptimizationDisplay: () => null }));
vi.mock('../ai/PlayerBehaviorAnalytics', () => ({ PlayerBehaviorAnalytics: () => null }));

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('AITradingDashboard scope errors (LEG-923)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces 403 scope detail on load failure', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.ai.view' },
        },
      }),
    );

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/admin\.ai\.view|Missing scope/i)).toBeTruthy();
    });
  });

  it('surfaces rate-limit copy on load 429', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<AITradingDashboard />);

    await waitFor(() => {
      const message = screen.getByText(/rate limit/i).textContent ?? '';
      expect(message).toMatch(/rate limit/i);
      expect(message).not.toMatch(/Failed to load AI trading data/);
    });
  });

  it('surfaces honest fallback on load GET TypeError/network collapse (LEG-2989)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AITradingDashboard />);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalled();
    });

    await waitFor(() => {
      const alert = document.querySelector('.alert-message');
      expect(alert).toBeTruthy();
      expect(alert?.textContent).toMatch(/Failed to load AI trading data/i);
    });

    const msg = document.querySelector('.alert-message')?.textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });
});
