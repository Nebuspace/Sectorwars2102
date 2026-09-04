import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), { response: { status } });

function mockLoads(holdingsPayload: unknown) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/pirate-holdings')) {
      return { data: holdingsPayload };
    }
    if (url.endsWith('/ships')) {
      return { data: { ships: [] } };
    }
    throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
  });
}

function renderSectorDetail() {
  return render(
    <MemoryRouter>
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />
    </MemoryRouter>,
  );
}

describe('SectorDetail pirate holdings (LEG-4178 / LEG-4196)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
  });

  it('fetches admin pirate-holdings during loadSectorDetails', async () => {
    mockLoads({ holdings: [] });

    renderSectorDetail();

    await waitFor(() => {
      expect(vi.mocked(api.get)).toHaveBeenCalledWith(
        '/api/v1/admin/sectors/42/pirate-holdings',
      );
    });
  });

  it('shows an honest empty state when holdings is empty', async () => {
    mockLoads({ holdings: [] });

    renderSectorDetail();

    expect(await screen.findByTestId('pirate-holdings-empty')).toHaveTextContent(
      'No pirate holdings in this sector.',
    );
    expect(screen.queryByTestId(/pirate-holding-row-/)).toBeNull();
    expect(screen.queryByText(/outlaw_base_id/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /capture|initiate/i })).toBeNull();
  });

  it('lists present holdings and deep-links outlaw_base_id when GET includes a non-null value', async () => {
    mockLoads({
      holdings: [
        {
          id: 'hold-1',
          tier: 'OUTPOST',
          owner_player_id: null,
          combat_lock_held_by: 'player-9',
          captured_at: '2026-09-03T12:00:00Z',
          outlaw_base_id: 'base-uuid-111',
          current_strength: 18,
          owner_team_id: 'team-7',
          region_id: 3,
          sector_id: 42,
        },
        {
          id: 'hold-2',
          tier: 'CAMP',
          owner_player_id: 'player-3',
          combat_lock_held_by: null,
          captured_at: null,
        },
      ],
    });

    renderSectorDetail();

    const row1 = await screen.findByTestId('pirate-holding-row-hold-1');
    expect(row1).toHaveTextContent('id: hold-1');
    expect(row1).toHaveTextContent('tier: OUTPOST');
    expect(row1).toHaveTextContent('owner: pirate-controlled');
    expect(row1).toHaveTextContent('combat lock: player-9');
    expect(row1).toHaveTextContent('captured_at: 2026-09-03T12:00:00Z');
    expect(row1).toHaveTextContent('current_strength: 18');
    expect(row1).toHaveTextContent('owner_team_id: team-7');
    expect(row1).toHaveTextContent('region_id: 3');
    expect(row1).toHaveTextContent('sector_id: 42');
    expect(row1).toHaveTextContent('outlaw_base_id: base-uuid-111');
    expect(row1).not.toHaveTextContent('must-not-render');

    const link = screen.getByTestId('pirate-holding-outlaw-base-link-hold-1');
    expect(link).toHaveAttribute('href', '/outlaw-bases/base-uuid-111');

    const row2 = screen.getByTestId('pirate-holding-row-hold-2');
    expect(row2).toHaveTextContent('owner: player-3');
    expect(row2).toHaveTextContent('combat lock: none');
    expect(row2).toHaveTextContent('captured_at: —');
    expect(row2).toHaveTextContent('current_strength: —');
    expect(row2).toHaveTextContent('owner_team_id: —');
    expect(row2).toHaveTextContent('region_id: —');
    expect(row2).toHaveTextContent('sector_id: —');
    expect(row2).toHaveTextContent('outlaw_base_id: —');
    expect(screen.queryByTestId('pirate-holding-outlaw-base-link-hold-2')).toBeNull();
    expect(screen.queryByRole('button', { name: /capture|initiate/i })).toBeNull();
  });

  it('uses honest placeholders when inspect keys are null (does not copy sector.sector_id)', async () => {
    mockLoads({
      holdings: [
        {
          id: 'hold-nulls',
          current_strength: null,
          owner_team_id: null,
          region_id: null,
          sector_id: null,
          outlaw_base_id: null,
        },
      ],
    });

    renderSectorDetail();

    const row = await screen.findByTestId('pirate-holding-row-hold-nulls');
    expect(row).toHaveTextContent('current_strength: —');
    expect(row).toHaveTextContent('owner_team_id: —');
    expect(row).toHaveTextContent('region_id: —');
    expect(row).toHaveTextContent('sector_id: —');
    expect(row).not.toHaveTextContent('sector_id: 42');
    expect(row).toHaveTextContent('outlaw_base_id: —');
    expect(row).not.toHaveTextContent('base-uuid-111');
    expect(screen.queryByTestId(/pirate-holding-outlaw-base-link-/)).toBeNull();
  });

  it('treats 404 as empty without a loadError alert', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/pirate-holdings')) {
        throw axiosError(404);
      }
      if (url.endsWith('/ships')) {
        return { data: { ships: [] } };
      }
      throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
    });

    renderSectorDetail();

    expect(await screen.findByTestId('pirate-holdings-empty')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('surfaces 403 via formatUniverseAdminError / noteLoadFailure', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/pirate-holdings')) {
        throw axiosError(403);
      }
      if (url.endsWith('/ships')) {
        return { data: { ships: [] } };
      }
      throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
    });

    renderSectorDetail();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/admin\.universe\.manage|Access denied/i);
    expect(await screen.findByTestId('pirate-holdings-empty')).toBeTruthy();
  });

  it('surfaces 429 via formatUniverseAdminError / noteLoadFailure', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/pirate-holdings')) {
        throw axiosError(429);
      }
      if (url.endsWith('/ships')) {
        return { data: { ships: [] } };
      }
      throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
    });

    renderSectorDetail();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
  });
});
