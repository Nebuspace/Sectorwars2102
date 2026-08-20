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
});
