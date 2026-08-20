import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PortDetail, { buildPortPatchPayload } from './StationDetail';
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

describe('buildPortPatchPayload (Soft-ORDER honesty)', () => {
  it('converts tax_rate percent UI value to fraction', () => {
    expect(buildPortPatchPayload('tax_rate', 10, basePort)).toEqual({ tax_rate: 0.1 });
  });

  it('maps owner_id / station_class — never owner_name / port_class', () => {
    expect(buildPortPatchPayload('owner_id', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', basePort)).toEqual({
      owner_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    });
    expect(buildPortPatchPayload('station_class', '4', basePort)).toEqual({ station_class: 4 });
  });

  it('maps defense drones to nested defenses.defense_drones', () => {
    expect(buildPortPatchPayload('defense_drones', 99, basePort)).toEqual({
      defenses: {
        defense_drones: 99,
        max_defense_drones: 50,
        shield_strength: 50,
        patrol_ships: 0,
      },
    });
  });

  it('maps commodity prices to commodities.*.current_price', () => {
    expect(buildPortPatchPayload('ore_price', 33, basePort)).toEqual({
      commodities: { ore: { current_price: 33 } },
    });
  });
});

describe('StationDetail Soft-ORDER PATCH payloads', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.patch).mockResolvedValue({ data: { success: true } });
  });

  it('saving tax_rate via labeled field sends fraction body', async () => {
    const user = userEvent.setup();
    render(<PortDetail port={basePort} onBack={() => undefined} />);

    const taxLabel = screen.getByText('Tax Rate:');
    const row = taxLabel.closest('.info-item');
    const clickable = row!.querySelector('.editable-field.clickable') as HTMLElement;
    await user.click(clickable);

    const input = screen.getByRole('spinbutton');
    await user.clear(input);
    await user.type(input, '10');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/ports/port-1', { tax_rate: 0.1 });
    });
  });

  it('saving owner sends owner_id not owner_name', async () => {
    const user = userEvent.setup();
    render(<PortDetail port={basePort} onBack={() => undefined} />);

    const ownerLabel = screen.getByText('Owner ID:');
    const row = ownerLabel.closest('.info-item');
    const clickable = row!.querySelector('.editable-field.clickable') as HTMLElement;
    await user.click(clickable);

    const input = screen.getByRole('textbox');
    await user.clear(input);
    await user.type(input, '22222222-2222-2222-2222-222222222222');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/ports/port-1', {
        owner_id: '22222222-2222-2222-2222-222222222222',
      });
    });
    expect(JSON.stringify(vi.mocked(api.patch).mock.calls[0][1])).not.toMatch(/owner_name|port_class/);
  });

  it('saving station class sends station_class not port_class', async () => {
    const user = userEvent.setup();
    render(<PortDetail port={basePort} onBack={() => undefined} />);

    const classLabel = screen.getByText('Station Class:');
    const row = classLabel.closest('.info-item');
    const clickable = row!.querySelector('.editable-field.clickable') as HTMLElement;
    await user.click(clickable);

    await user.selectOptions(screen.getByRole('combobox'), '5');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/ports/port-1', { station_class: 5 });
    });
  });

  it('saving ore price sends nested commodities current_price', async () => {
    const user = userEvent.setup();
    render(<PortDetail port={basePort} onBack={() => undefined} />);

    const buyLabels = screen.getAllByText('Buy:');
    const oreBuy = buyLabels[0].closest('.buy-price');
    const clickable = oreBuy!.querySelector('.editable-field.clickable') as HTMLElement;
    await user.click(clickable);

    const input = screen.getByRole('spinbutton');
    fireEvent.change(input, { target: { value: '42' } });
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/ports/port-1', {
        commodities: { ore: { current_price: 42 } },
      });
    });
  });
});
