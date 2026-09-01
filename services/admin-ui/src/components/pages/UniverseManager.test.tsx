import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
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
