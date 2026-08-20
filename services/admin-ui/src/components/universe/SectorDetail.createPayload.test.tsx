import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SectorDetail from './SectorDetail';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    error: vi.fn(),
    success: vi.fn(),
    info: vi.fn(),
  }),
}));

const sector = {
  id: 'sec-uuid-1',
  sector_id: 42,
  name: 'Alpha',
  type: 'STANDARD',
  x_coord: 0,
  y_coord: 0,
  z_coord: 0,
  hazard_level: 1,
  is_discovered: true,
  has_port: false,
  has_planet: false,
  controlling_faction: null,
};

describe('SectorDetail Soft-ORDER create payload honesty', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.get).mockRejectedValue(new Error('404'));
    vi.mocked(api.post).mockResolvedValue({ data: { id: 'new' }, status: 200 });
  });

  it('Create Station POSTs StationCreateRequest keys (station_class+type, no port_class/tax/prices)', async () => {
    const user = userEvent.setup();
    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /\+ Create Station/i })).toBeTruthy();
    });

    await user.click(screen.getByRole('button', { name: /\+ Create Station/i }));
    await user.type(screen.getByPlaceholderText(/station name/i), 'Hub One');
    await user.click(screen.getByRole('button', { name: /^Create Station$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe('/api/v1/admin/sectors/42/port');
    expect(body).toEqual({
      name: 'Hub One',
      station_class: 6,
      type: 'TRADING',
    });
    expect(body).not.toHaveProperty('port_class');
    expect(body).not.toHaveProperty('tax_rate');
    expect(body).not.toHaveProperty('defense_fighters');
    expect(body).not.toHaveProperty('ore_price');
  });

  it('Create Planet POSTs PlanetCreateRequest (type not planet_type; no citadel fields)', async () => {
    const user = userEvent.setup();
    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /\+ Create Planet/i })).toBeTruthy();
    });

    await user.click(screen.getByRole('button', { name: /\+ Create Planet/i }));
    expect(screen.queryByText(/M-Class/i)).toBeNull();
    expect(screen.getByRole('option', { name: 'TERRAN' })).toBeTruthy();

    await user.type(screen.getByPlaceholderText(/planet name/i), 'Nova World');
    await user.click(screen.getByRole('button', { name: /^Create Planet$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe('/api/v1/admin/sectors/42/planet');
    expect(body).toEqual({
      name: 'Nova World',
      type: 'TERRAN',
    });
    expect(body).not.toHaveProperty('planet_type');
    expect(body).not.toHaveProperty('citadel_level');
    expect(body).not.toHaveProperty('shield_level');
    expect(body).not.toHaveProperty('drones');
    expect(body).not.toHaveProperty('breeding_rate');
  });
});

describe('SectorDetail Soft-ORDER SectorType + controlling_faction honesty', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.get).mockRejectedValue(new Error('404'));
    vi.mocked(api.put).mockResolvedValue({ data: {}, status: 200 });
  });

  it('type select options include STANDARD not NORMAL and ⊆ tip SectorType', async () => {
    const user = userEvent.setup();
    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('STANDARD')).toBeTruthy();
    });

    await user.click(screen.getByText('STANDARD'));
    const select = await screen.findByRole('combobox');
    const optionTexts = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(optionTexts).toContain('STANDARD');
    expect(optionTexts).not.toContain('NORMAL');
    expect(optionTexts).toContain('ANOMALY');
    expect(optionTexts).toContain('BLACK_HOLE');
    expect(optionTexts).toContain('RADIATION_ZONE');
  });

  it('saving controlling_faction None/empty PUTs null not string None', async () => {
    const user = userEvent.setup();
    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('None')).toBeTruthy();
    });

    await user.click(screen.getByText('None'));
    const input = await screen.findByRole('textbox');
    expect((input as HTMLInputElement).value).toBe('None');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });

    const [url, body] = vi.mocked(api.put).mock.calls[0];
    expect(url).toBe('/api/v1/admin/sectors/sec-uuid-1');
    expect(body).toEqual({ controlling_faction: null });
  });
});
