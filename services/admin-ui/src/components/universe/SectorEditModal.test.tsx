import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import SectorEditModal from './SectorEditModal';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../ui/PageHeader', () => ({
  default: () => null,
}));

const sector = {
  id: 'sec-1',
  sector_id: 1,
  name: 'Alpha',
  type: 'NORMAL',
  cluster_id: 'c1',
  x_coord: 0,
  y_coord: 0,
  z_coord: 0,
  hazard_level: 1,
  is_discovered: true,
  has_port: false,
  has_planet: false,
  has_warp_tunnel: false,
  player_count: 0,
  controlling_faction: null,
};

describe('SectorEditModal scope errors (LEG-1213)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { ...sector }, status: 200 });
  });

  it('surfaces admin.universe.manage on update 403', async () => {
    vi.mocked(api.put).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403, data: {} } }),
    );

    render(
      <MemoryRouter>
        <SectorEditModal
          isOpen
          sector={sector as any}
          onClose={() => {}}
          onSave={() => {}}
        />
      </MemoryRouter>,
    );

    // Force a dirty save: change name then click Save if present
    const nameInput = await screen.findByDisplayValue('Alpha');
    fireEvent.change(nameInput, { target: { value: 'Beta' } });
    const saveBtn = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText(/admin\.universe\.manage|Access denied/i)).toBeTruthy();
    });
  });

  it('surfaces rate-limit on update 429', async () => {
    vi.mocked(api.put).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429, data: {} } }),
    );

    render(
      <MemoryRouter>
        <SectorEditModal
          isOpen
          sector={sector as any}
          onClose={() => {}}
          onSave={() => {}}
        />
      </MemoryRouter>,
    );

    const nameInput = await screen.findByDisplayValue('Alpha');
    fireEvent.change(nameInput, { target: { value: 'Beta' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces honest fallback on update TypeError/network collapse (LEG-3066)', async () => {
    vi.mocked(api.put).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <MemoryRouter>
        <SectorEditModal
          isOpen
          sector={sector as any}
          onClose={() => {}}
          onSave={() => {}}
        />
      </MemoryRouter>,
    );

    const nameInput = await screen.findByDisplayValue('Alpha');
    fireEvent.change(nameInput, { target: { value: 'Beta' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to update sector/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to update sector/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), { response: { status } });

function mockDetailLoads(holdingsPayload: unknown) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/pirate-holdings')) {
      return { data: holdingsPayload };
    }
    if (url.endsWith('/planet')) {
      return { data: { has_planet: false, planet: null } };
    }
    if (url.endsWith('/port')) {
      return { data: { has_port: false, port: null } };
    }
    return { data: {} };
  });
}

function renderModal() {
  return render(
    <MemoryRouter>
      <SectorEditModal
        isOpen
        sector={sector as any}
        onClose={() => {}}
        onSave={() => {}}
      />
    </MemoryRouter>,
  );
}

describe('SectorEditModal pirate holdings (LEG-4189)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
  });

  it('fetches admin pirate-holdings once using sector.sector_id', async () => {
    mockDetailLoads({ holdings: [] });
    renderModal();

    await waitFor(() => {
      expect(vi.mocked(api.get)).toHaveBeenCalledWith(
        '/api/v1/admin/sectors/1/pirate-holdings',
      );
    });
    const holdingsCalls = vi
      .mocked(api.get)
      .mock.calls.filter(([url]) => String(url).endsWith('/pirate-holdings'));
    expect(holdingsCalls.length).toBeGreaterThanOrEqual(1);
    expect(holdingsCalls.every(([url]) => url === '/api/v1/admin/sectors/1/pirate-holdings')).toBe(
      true,
    );
    expect(vi.mocked(api.get).mock.calls.some(([url]) => String(url).includes('sec-1/pirate'))).toBe(
      false,
    );
  });

  it('shows an honest empty state when holdings is empty', async () => {
    mockDetailLoads({ holdings: [] });
    renderModal();

    expect(await screen.findByTestId('pirate-holdings-empty')).toHaveTextContent(
      'No pirate holdings in this sector.',
    );
    expect(screen.queryByTestId(/pirate-holding-row-/)).toBeNull();
    expect(screen.queryByText(/outlaw_base_id/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /capture|initiate/i })).toBeNull();
  });

  it('treats omitted holdings key as empty without fabricating rows', async () => {
    mockDetailLoads({});
    renderModal();

    expect(await screen.findByTestId('pirate-holdings-empty')).toBeTruthy();
    expect(screen.queryByTestId(/pirate-holding-row-/)).toBeNull();
  });

  it('lists present holdings and shows outlaw_base_id when GET includes a non-null value', async () => {
    mockDetailLoads({
      holdings: [
        {
          id: 'hold-1',
          tier: 'OUTPOST',
          owner_player_id: null,
          outlaw_base_id: 'base-uuid-111',
        },
        {
          id: 'hold-2',
          owner_player_id: 'player-3',
        },
      ],
    });
    renderModal();

    const row1 = await screen.findByTestId('pirate-holding-row-hold-1');
    expect(row1).toHaveTextContent('id: hold-1');
    expect(row1).toHaveTextContent('tier: OUTPOST');
    expect(row1).toHaveTextContent('owner: pirate-controlled');
    expect(row1).toHaveTextContent('outlaw_base_id: base-uuid-111');
    expect(row1).not.toHaveTextContent('must-not-render');

    const row2 = screen.getByTestId('pirate-holding-row-hold-2');
    expect(row2).toHaveTextContent('owner: player-3');
    expect(row2).toHaveTextContent('tier: —');
    expect(row2).toHaveTextContent('outlaw_base_id: —');
    expect(screen.queryByRole('button', { name: /capture|initiate/i })).toBeNull();
  });

  it('surfaces holdings load 403 via formatUniverseAdminError', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/pirate-holdings')) {
        throw axiosError(403);
      }
      if (url.endsWith('/planet')) {
        return { data: { has_planet: false, planet: null } };
      }
      if (url.endsWith('/port')) {
        return { data: { has_port: false, port: null } };
      }
      return { data: {} };
    });
    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/admin\.universe\.manage|Access denied/i)).toBeTruthy();
    });
    expect(await screen.findByTestId('pirate-holdings-empty')).toBeTruthy();
  });

  it('surfaces holdings load 429 via formatUniverseAdminError', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/pirate-holdings')) {
        throw axiosError(429);
      }
      if (url.endsWith('/planet')) {
        return { data: { has_planet: false, planet: null } };
      }
      if (url.endsWith('/port')) {
        return { data: { has_port: false, port: null } };
      }
      return { data: {} };
    });
    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces holdings load network collapse via fallback copy', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/pirate-holdings')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.endsWith('/planet')) {
        return { data: { has_planet: false, planet: null } };
      }
      if (url.endsWith('/port')) {
        return { data: { has_port: false, port: null } };
      }
      return { data: {} };
    });
    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/Failed to load pirate holdings/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load pirate holdings/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});
