import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PortDetail from './StationDetail';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    patch: vi.fn(),
  },
}));

const basePort = {
  id: 'port-1',
  name: 'Outpost Alpha',
  station_class: 2,
  owner_id: '11111111-1111-1111-1111-111111111111',
  tax_rate: 0.05,
  defense_drones: 40,
  ore_quantity: 100,
  ore_price: 25,
  organics_price: 15,
  equipment_price: 50,
};

async function saveName(value: string) {
  fireEvent.click(screen.getByText('Outpost Alpha'));
  const input = await screen.findByDisplayValue('Outpost Alpha');
  fireEvent.change(input, { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: '✓' }));
}

/**
 * LEG-3457 Soft-ORDER — StationDetail TypeError/Network Error honesty densify.
 */
describe('StationDetail typeErrorHonesty densify (LEG-3457)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on name PATCH to update-name fallback', async () => {
    vi.mocked(api.patch).mockRejectedValue(new Error('Network Error'));

    render(<PortDetail port={basePort} onBack={() => {}} />);
    await saveName('Renamed Network');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to update name/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on name PATCH to update-name fallback', async () => {
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PortDetail port={basePort} onBack={() => {}} />);
    await saveName('Renamed TypeError');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to update name/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
  });
});
