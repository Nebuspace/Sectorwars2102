import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { PredictiveAnalytics } from './PredictiveAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const samplePrediction = {
  commodity: 'ore',
  station_id: 'station-abc',
  current_price: 100,
  predicted_price: 110,
  price_change_pct: 10,
  trend: 'rising',
  confidence: 0.82,
  volatility: 0.05,
  lower_bound: 95,
  upper_bound: 115,
  prediction_horizon_hours: 1,
  factors: ['rising demand', 'low supply'],
  timestamp: '2026-09-01T12:00:00Z',
};

const livePayload = {
  timeframe: '1h',
  hours_ahead: 1,
  resource: null,
  station_id: null,
  predictions: [samplePrediction],
  count: 1,
  generated_at: '2026-09-01T12:05:00Z',
};

describe('PredictiveAnalytics (LEG-3612)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loads predictions from GET /admin/analytics/predictions and renders rows', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: livePayload });

    render(<PredictiveAnalytics />);

    await waitFor(() => {
      expect(screen.getByText('ore')).toBeTruthy();
    });

    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/analytics/predictions', {
      params: { timeframe: '1h' },
    });

    expect(screen.getByText('100.00')).toBeTruthy();
    expect(screen.getByText('110.00')).toBeTruthy();
    expect(screen.getByText(/82% confidence/i)).toBeTruthy();
    expect(screen.getByText('rising demand')).toBeTruthy();
    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });

  it('shows honest empty state when count is zero', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        ...livePayload,
        predictions: [],
        count: 0,
      },
    });

    render(<PredictiveAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toMatch(/No predictions returned/i);
    });
    expect(screen.getByRole('status').textContent).toMatch(/insufficient market history/i);
  });

  it('collapses axios Network Error without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<PredictiveAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load market predictions/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PredictiveAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load market predictions/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('refetches when timeframe selector changes', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: livePayload });

    render(<PredictiveAnalytics />);
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: '4h' }));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/analytics/predictions', {
        params: { timeframe: '4h' },
      });
    });
  });
});
