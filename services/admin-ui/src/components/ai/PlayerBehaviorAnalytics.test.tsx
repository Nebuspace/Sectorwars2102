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

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('PlayerBehaviorAnalytics scope honesty (LEG-1206)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports 403 as PLAYERS_VIEW denial', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/PLAYERS_VIEW|Access denied/i);
    expect(alert).not.toMatch(/Failed to load behavior analytics/i);
  });

  it('reports 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<PlayerBehaviorAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });
});
