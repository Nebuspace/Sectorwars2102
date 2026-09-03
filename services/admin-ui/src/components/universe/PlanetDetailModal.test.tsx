import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlanetDetailModal from './PlanetDetailModal';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

const planet = {
  id: 'p1',
  name: 'Terra',
  sector_id: 's1',
  planet_type: 'TERRAN',
  population: 100,
  max_population: 1000,
  defense_level: 1,
  habitability_score: 50,
  resource_richness: 1,
  gravity: 1,
};

describe('PlanetDetailModal scope errors (LEG-1214)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { holdings: [] } });
  });

  it('surfaces admin.universe.manage on save 403', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403, data: {} } }),
    );

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/admin\.universe\.manage|Access denied/i)).toBeTruthy();
    });
  });

  it('surfaces rate-limit on save 429', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429, data: {} } }),
    );

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces honest fallback on save TypeError/network collapse (LEG-3062)', async () => {
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to save planet changes/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to save planet changes/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('PlanetDetailModal Soft-ORDER defense_level (LEG-1462)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.patch).mockResolvedValue({ data: {} });
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { holdings: [] } });
  });

  it('includes defense_level in PATCH payload', async () => {
    render(
      <PlanetDetailModal
        isOpen
        planet={{ ...planet, defense_level: 42 } as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/api/v1/admin/planets/p1',
        expect.objectContaining({ defense_level: 42 }),
      );
    });
  });
});

describe('PlanetDetailModal Soft-ORDER demote non-PATCHABLE (LEG-1471)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.patch).mockResolvedValue({ data: {} });
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { holdings: [] } });
  });

  it('keeps population/max_population/atmosphere display-only and omits them from PATCH', async () => {
    render(
      <PlanetDetailModal
        isOpen
        planet={{ ...planet, population: 777, max_population: 888, atmosphere: 'thin' } as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    expect(screen.getByText('777')).toBeTruthy();
    expect(screen.getByText('888')).toBeTruthy();
    expect(screen.getByText('thin')).toBeTruthy();
    expect(screen.queryByPlaceholderText(/planetary atmosphere/i)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalled();
    });
    const payload = vi.mocked(api.patch).mock.calls[0][1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('population');
    expect(payload).not.toHaveProperty('max_population');
    expect(payload).not.toHaveProperty('atmosphere');
    expect(payload).toEqual(
      expect.objectContaining({
        name: 'Terra',
        defense_level: 1,
      }),
    );
  });
});

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), { response: { status } });

const holdingPlanet = {
  ...planet,
  sector_id: 42,
};

function renderHoldingsModal() {
  return render(
    <PlanetDetailModal
      isOpen
      planet={holdingPlanet as any}
      onClose={() => {}}
      mode="view"
    />,
  );
}

describe('PlanetDetailModal pirate holdings (LEG-4190)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.patch).mockReset();
  });

  it('fetches admin pirate-holdings using planet.sector_id', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { holdings: [] } });
    renderHoldingsModal();

    await waitFor(() => {
      expect(vi.mocked(api.get)).toHaveBeenCalledWith(
        '/api/v1/admin/sectors/42/pirate-holdings',
      );
    });
    expect(
      vi.mocked(api.get).mock.calls.every(
        ([url]) => url === '/api/v1/admin/sectors/42/pirate-holdings',
      ),
    ).toBe(true);
  });

  it('shows an honest empty state when holdings is empty', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { holdings: [] } });
    renderHoldingsModal();

    expect(await screen.findByTestId('pirate-holdings-empty')).toHaveTextContent(
      'No pirate holdings in this sector.',
    );
    expect(screen.queryByTestId(/pirate-holding-row-/)).toBeNull();
    expect(screen.queryByText(/outlaw_base_id/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /capture|initiate/i })).toBeNull();
  });

  it('treats omitted holdings key as empty without fabricating rows', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {} });
    renderHoldingsModal();

    expect(await screen.findByTestId('pirate-holdings-empty')).toBeTruthy();
    expect(screen.queryByTestId(/pirate-holding-row-/)).toBeNull();
  });

  it('lists present holdings without inventing outlaw_base_id', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        holdings: [
          {
            id: 'hold-1',
            tier: 'OUTPOST',
            owner_player_id: null,
            outlaw_base_id: 'must-not-render',
          },
          { id: 'hold-2', owner_player_id: 'player-3' },
        ],
      },
    });
    renderHoldingsModal();

    const row1 = await screen.findByTestId('pirate-holding-row-hold-1');
    expect(row1).toHaveTextContent('id: hold-1');
    expect(row1).toHaveTextContent('tier: OUTPOST');
    expect(row1).toHaveTextContent('owner: pirate-controlled');
    expect(row1).not.toHaveTextContent('must-not-render');
    expect(row1).not.toHaveTextContent('outlaw_base_id');

    const row2 = screen.getByTestId('pirate-holding-row-hold-2');
    expect(row2).toHaveTextContent('owner: player-3');
    expect(row2).toHaveTextContent('tier: —');
    expect(screen.queryByRole('button', { name: /capture|initiate/i })).toBeNull();
  });

  it('surfaces holdings load 403 via formatUniverseAdminError', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));
    renderHoldingsModal();

    await waitFor(() => {
      expect(screen.getByText(/admin\.universe\.manage|Access denied/i)).toBeTruthy();
    });
    expect(await screen.findByTestId('pirate-holdings-empty')).toBeTruthy();
  });
});
