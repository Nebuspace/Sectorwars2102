import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import EconomyLeversPanel from './EconomyLeversPanel';
import { api } from '../../utils/auth';

const toastError = vi.hoisted(() => vi.fn());

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

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
  ship_specs: [{ type: 'Frigate', base_cost: 5000, is_npc_only: false }],
  upgrades: [
    {
      type: 'CargoBay',
      base_cost: 1000,
      cost_multiplier: 1.5,
      description: 'Extra cargo',
    },
  ],
  bounty_payout_ratio: 1.0,
  insurance_premium_pct: { BASIC: 0.05, STANDARD: 0.1, PREMIUM: 0.15 },
  insurance_net_payout_pct: { BASIC: 0.5, STANDARD: 0.7, PREMIUM: 0.9 },
  station_commodities: [],
};

function assertNoTransportLeak() {
  const dom = document.body.textContent ?? '';
  expect(dom).not.toBe('Network Error');
  expect(dom).not.toContain('Network Error');
  expect(dom).not.toMatch(/TypeError/i);
  expect(dom).not.toMatch(/Failed to fetch/i);
}

/**
 * LEG-3657 Soft-ORDER — EconomyLeversPanel TypeError/Network Error honesty densify.
 */
describe('EconomyLeversPanel typeErrorHonesty densify (LEG-3657)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on initial load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/Gameserver unreachable|network error loading economy levers/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    assertNoTransportLeak();
  });

  it('collapses TypeError Failed to fetch on initial load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/Gameserver unreachable|network error loading economy levers/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
    assertNoTransportLeak();
  });

  it('collapses axios Network Error on insurance save without leaking transport text', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: emptySnapshot });
    vi.mocked(api.patch).mockRejectedValue(new Error('Network Error'));

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText('BASIC insurance premium percent')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('BASIC insurance premium percent'), {
      target: { value: '12' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save insurance levers' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/Failed to save insurance levers/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    assertNoTransportLeak();
  });

  it('collapses TypeError Failed to fetch on insurance save without leaking transport text', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: emptySnapshot });
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<EconomyLeversPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText('BASIC insurance premium percent')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('BASIC insurance premium percent'), {
      target: { value: '12' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save insurance levers' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/Failed to save insurance levers/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
    assertNoTransportLeak();
  });
});
