import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import EconomyLeversPanel from './EconomyLeversPanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

const toastError = vi.fn();
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

const emptySnapshot = {
  regions: [],
  ship_specs: [],
  upgrades: [],
  bounty_payout_ratio: 1.0,
  insurance_premium_pct: { BASIC: 0.05, STANDARD: 0.1, PREMIUM: 0.15 },
  insurance_net_payout_pct: { BASIC: 0.5, STANDARD: 0.7, PREMIUM: 0.9 },
  station_commodities: [
    {
      station_id: 's1',
      station_name: 'Alpha Dock',
      commodity: 'Ore',
      base_price: 100,
      production_rate: 1.5,
    },
  ],
};

describe('EconomyLeversPanel (LEG-30)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    toastError.mockReset();
  });

  it('loads bounty / insurance / commodity levers and patches bounty ratio', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: emptySnapshot });
    vi.mocked(api.patch).mockResolvedValue({ data: { bounty_payout_ratio: 1.25 } });

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText('Bounty payout ratio')).toBeTruthy();
    });
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/economy/levers');
    expect(screen.getByText('Alpha Dock')).toBeTruthy();
    expect(screen.getByLabelText('BASIC insurance premium percent')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Bounty payout ratio'), {
      target: { value: '1.25' },
    });
    const bountyRow = screen.getByLabelText('Bounty payout ratio').closest('tr');
    expect(bountyRow).toBeTruthy();
    fireEvent.click(bountyRow!.querySelector('button')!);

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/economy/levers/bounty-payout', {
        bounty_payout_ratio: 1.25,
      });
    });
  });

  it('patches insurance premium and net-payout for tip tiers', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: emptySnapshot });
    vi.mocked(api.patch).mockResolvedValue({ data: {} });

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText('BASIC insurance premium percent')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('BASIC insurance premium percent'), {
      target: { value: '12' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save insurance levers' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/economy/levers/insurance', {
        insurance_premium_pct: { BASIC: 0.12, STANDARD: 0.1, PREMIUM: 0.15 },
        insurance_net_payout_pct: { BASIC: 0.5, STANDARD: 0.7, PREMIUM: 0.9 },
      });
    });
  });

  it('patches station commodity base_price and production_rate', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: emptySnapshot });
    vi.mocked(api.patch).mockResolvedValue({
      data: { base_price: 120, production_rate: 2 },
    });

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText('Base price for Alpha Dock Ore')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Base price for Alpha Dock Ore'), {
      target: { value: '120' },
    });
    fireEvent.change(screen.getByLabelText('Production rate for Alpha Dock Ore'), {
      target: { value: '2' },
    });
    const commodityRow = screen.getByLabelText('Base price for Alpha Dock Ore').closest('tr');
    expect(commodityRow).toBeTruthy();
    fireEvent.click(commodityRow!.querySelector('button')!);

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/api/v1/admin/economy/levers/stations/s1/commodities/Ore',
        { base_price: 120, production_rate: 2 }
      );
    });
  });
  it('reports a 403 as ECONOMY_MANAGE on load via toast', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } });

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/ECONOMY_MANAGE/);
    expect(msg).not.toBe('Failed to load economy levers');
  });

  it('reports a 429 as an admin rate-limit on load via toast', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 429 } });

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/rate limit/i);
  });
});
