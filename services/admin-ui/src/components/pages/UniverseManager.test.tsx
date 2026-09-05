import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import UniverseManager from './UniverseManager';

const mockAdmin = vi.hoisted(() => ({
  galaxyState: null as {
    id: string;
    name: string;
    statistics: {
      total_sectors: number;
      discovered_sectors: number;
      station_count: number;
      planet_count: number;
      warp_tunnel_count: number;
      sector_warp_count: number;
    };
  } | null,
  regions: [] as Array<Record<string, unknown>>,
  sectors: [] as Array<Record<string, unknown>>,
  loadGalaxyInfo: vi.fn(),
  loadSectors: vi.fn(),
  loadRegions: vi.fn(),
  isLoading: false,
  error: null as string | null,
}));

vi.mock('../../contexts/AdminContext', () => ({
  useAdmin: () => mockAdmin,
}));

vi.mock('../universe/SectorDetail', () => ({
  default: () => null,
}));

vi.mock('../universe/StationDetail', () => ({
  default: () => null,
}));

vi.mock('../universe/PlanetDetail', () => ({
  default: () => null,
}));

vi.mock('../universe/PlaceGoldBubblePanel', () => ({
  default: () => <div data-testid="place-gold-bubble-panel-stub" />,
}));

const sampleGalaxyState = {
  id: 'g1',
  name: 'Andromeda Prime',
  statistics: {
    total_sectors: 500,
    discovered_sectors: 120,
    station_count: 40,
    planet_count: 80,
    warp_tunnel_count: 12,
    sector_warp_count: 900,
  },
};

function renderUniverse() {
  return render(
    <MemoryRouter>
      <UniverseManager />
    </MemoryRouter>,
  );
}

function errorStripText(): string {
  return document.querySelector('.error-message')?.textContent ?? '';
}

function listSector(overrides: Record<string, unknown> = {}) {
  return {
    id: 's1',
    sector_id: 1,
    name: 'Alpha',
    type: 'STANDARD',
    x_coord: 0,
    y_coord: 0,
    z_coord: 0,
    hazard_level: 1,
    has_port: false,
    has_planet: false,
    has_warp_tunnel: false,
    ...overrides,
  };
}

describe('UniverseManager', () => {
  beforeEach(() => {
    mockAdmin.galaxyState = { ...sampleGalaxyState };
    mockAdmin.regions = [];
    mockAdmin.sectors = [];
    mockAdmin.isLoading = false;
    mockAdmin.error = null;
    mockAdmin.loadGalaxyInfo.mockReset();
    mockAdmin.loadSectors.mockReset();
    mockAdmin.loadRegions.mockReset();
  });

  it('renders galaxy overview on happy path without error strip', () => {
    renderUniverse();

    expect(screen.getByText('Andromeda Prime')).toBeTruthy();
    expect(screen.getByText('500')).toBeTruthy();
    expect(document.querySelector('.error-message')).toBeNull();
  });

  it('surfaces admin.galaxy.manage scope denial on error strip (403)', () => {
    mockAdmin.error = 'Access denied — requires the admin.galaxy.manage scope.';

    renderUniverse();

    expect(errorStripText()).toMatch(/admin\.galaxy\.manage/);
    expect(errorStripText()).not.toMatch(/^Failed to load galaxy information$/);
  });

  it('surfaces admin rate-limit copy on error strip (429)', () => {
    mockAdmin.error = 'Admin rate limit exceeded — wait a moment and try again.';

    renderUniverse();

    expect(errorStripText()).toMatch(/rate limit/i);
    expect(errorStripText()).not.toMatch(/^Failed to load galaxy information$/);
  });

  it('surfaces honest fallback on TypeError/network collapse (LEG-3123)', () => {
    mockAdmin.error = 'Failed to load galaxy information';

    renderUniverse();

    expect(errorStripText()).toMatch(/Failed to load galaxy information/i);
    expect(errorStripText()).not.toMatch(/TypeError/i);
    expect(errorStripText()).not.toMatch(/Failed to fetch/i);
  });
});

describe('UniverseManager pirate holding badge/ring (LEG-4184)', () => {
  beforeEach(() => {
    mockAdmin.galaxyState = { ...sampleGalaxyState };
    mockAdmin.regions = [];
    mockAdmin.sectors = [];
    mockAdmin.isLoading = false;
    mockAdmin.error = null;
    mockAdmin.loadGalaxyInfo.mockReset();
    mockAdmin.loadSectors.mockReset();
    mockAdmin.loadRegions.mockReset();
  });

  it('shows a Holding grid badge only when has_pirate_holding is true', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [listSector({ name: 'Corsair', has_pirate_holding: true })];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Sectors/i }));

    expect(screen.getByText('Corsair')).toBeTruthy();
    expect(screen.getByTitle('Pirate Holding')).toHaveTextContent('Holding');
  });

  it('does not show a Holding grid badge when has_pirate_holding is false', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [listSector({ name: 'Beta', has_pirate_holding: false })];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Sectors/i }));

    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.queryByTitle('Pirate Holding')).toBeNull();
  });

  it('does not show a Holding grid badge when has_pirate_holding is omitted', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [listSector({ name: 'Gamma' })];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Sectors/i }));

    expect(screen.getByText('Gamma')).toBeTruthy();
    expect(screen.queryByTitle('Pirate Holding')).toBeNull();
  });

  it('shows map ring + tooltip Holding line only when has_pirate_holding is true', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [listSector({ name: 'Corsair', has_pirate_holding: true })];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Galaxy Map/i }));

    expect(screen.getByTestId('pirate-holding-ring-s1')).toBeTruthy();
    const ringGroup = screen.getByTestId('pirate-holding-ring-s1').closest('g');
    expect(ringGroup).toBeTruthy();
    fireEvent.mouseEnter(ringGroup!);
    expect(screen.getByTestId('tooltip-has-holding')).toHaveTextContent('Has Holding');
  });

  it('omits map ring and Holding tooltip when has_pirate_holding is omitted', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [listSector({ name: 'Quiet' })];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Galaxy Map/i }));

    expect(screen.queryByTestId('pirate-holding-ring-s1')).toBeNull();
    const svg = document.querySelector('svg');
    const sectorGroups = Array.from(svg?.querySelectorAll('g') ?? []).filter(
      (g) => g.querySelector('circle[fill]') && !g.querySelector('line'),
    );
    expect(sectorGroups.length).toBeGreaterThan(0);
    fireEvent.mouseEnter(sectorGroups[sectorGroups.length - 1]);
    expect(screen.queryByTestId('tooltip-has-holding')).toBeNull();
  });
});

describe('UniverseManager pirate holding filter (LEG-4194)', () => {
  beforeEach(() => {
    mockAdmin.galaxyState = { ...sampleGalaxyState };
    mockAdmin.regions = [];
    mockAdmin.sectors = [];
    mockAdmin.isLoading = false;
    mockAdmin.error = null;
    mockAdmin.loadGalaxyInfo.mockReset();
    mockAdmin.loadSectors.mockReset();
    mockAdmin.loadRegions.mockReset();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({}),
    } as Response);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('hides false and omitted holdings when Has Holding Yes is enabled', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [
      listSector({ id: 's1', sector_id: 1, name: 'Corsair', has_pirate_holding: true }),
      listSector({ id: 's2', sector_id: 2, name: 'Beta', has_pirate_holding: false }),
      listSector({ id: 's3', sector_id: 3, name: 'Gamma' }),
    ];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Sectors/i }));
    expect(screen.getByText('Corsair')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.getByText('Gamma')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Has Holding Yes' }));

    expect(screen.getByText('Corsair')).toBeTruthy();
    expect(screen.queryByText('Beta')).toBeNull();
    expect(screen.queryByText('Gamma')).toBeNull();
    expect(vi.mocked(globalThis.fetch).mock.calls.every(([url]) => !String(url).includes('pirate-holdings'))).toBe(
      true,
    );
  });

  it('shows honest empty when the filter is on and none match', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [
      listSector({ id: 's2', sector_id: 2, name: 'Beta', has_pirate_holding: false }),
      listSector({ id: 's3', sector_id: 3, name: 'Gamma' }),
    ];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Sectors/i }));
    await user.click(screen.getByRole('button', { name: 'Has Holding Yes' }));

    expect(screen.getByTestId('universe-holding-filter-empty')).toHaveTextContent(
      'No sectors with pirate holdings.',
    );
    expect(screen.queryByText(/Generate a galaxy first/i)).toBeNull();
    expect(screen.queryByText('Beta')).toBeNull();
  });

  it('hides non-holding map dots when Has Holding Yes is enabled', async () => {
    const user = userEvent.setup();
    mockAdmin.sectors = [
      listSector({ id: 's1', sector_id: 1, name: 'Corsair', has_pirate_holding: true }),
      listSector({ id: 's2', sector_id: 2, name: 'Quiet', x_coord: 10, y_coord: 10 }),
    ];

    renderUniverse();
    await user.click(screen.getByRole('button', { name: /Galaxy Map/i }));
    expect(screen.getByTestId('pirate-holding-ring-s1')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Has Holding Yes' }));

    expect(screen.getByTestId('pirate-holding-ring-s1')).toBeTruthy();
    expect(screen.getByText(/1 sectors/)).toBeTruthy();
    expect(vi.mocked(globalThis.fetch).mock.calls.every(([url]) => !String(url).includes('pirate-holdings'))).toBe(
      true,
    );
  });
});
