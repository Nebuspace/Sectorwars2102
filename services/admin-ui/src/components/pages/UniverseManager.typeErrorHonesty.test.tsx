import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import UniverseManager from './UniverseManager';
import { adminHttpErrorMessage } from '../../utils/adminHttpError';

const mockAdmin = vi.hoisted(() => ({
  galaxyState: {
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
  } as {
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

/**
 * LEG-3658 Soft-ORDER — UniverseManager TypeError/Network Error densify.
 * Universe overview and sector list errors surface via AdminContext error strip.
 */
describe('UniverseManager typeErrorHonesty densify (LEG-3658)', () => {
  beforeEach(() => {
    mockAdmin.galaxyState = {
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
    mockAdmin.regions = [];
    mockAdmin.sectors = [];
    mockAdmin.isLoading = false;
    mockAdmin.error = null;
    mockAdmin.loadGalaxyInfo.mockReset();
    mockAdmin.loadSectors.mockReset();
    mockAdmin.loadRegions.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on galaxy load without leaking raw transport text', async () => {
    mockAdmin.galaxyState = null;
    mockAdmin.loadGalaxyInfo.mockImplementation(async () => {
      mockAdmin.error = adminHttpErrorMessage(
        new Error('Network Error'),
        'Failed to load galaxy information',
        'admin.galaxy.manage',
      );
    });

    const { rerender } = renderUniverse();
    await waitFor(() => expect(mockAdmin.loadGalaxyInfo).toHaveBeenCalled());
    rerender(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(errorStripText()).toMatch(/Failed to load galaxy information/i);
    });
    expect(errorStripText()).not.toMatch(/Network Error/i);
    expect(errorStripText()).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on galaxy load without leaking transport text', async () => {
    mockAdmin.galaxyState = null;
    mockAdmin.loadGalaxyInfo.mockImplementation(async () => {
      mockAdmin.error = adminHttpErrorMessage(
        new TypeError('Failed to fetch'),
        'Failed to load galaxy information',
        'admin.galaxy.manage',
      );
    });

    const { rerender } = renderUniverse();
    await waitFor(() => expect(mockAdmin.loadGalaxyInfo).toHaveBeenCalled());
    rerender(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(errorStripText()).toMatch(/Failed to load galaxy information/i);
    });
    expect(errorStripText()).not.toMatch(/Failed to fetch/i);
    expect(errorStripText()).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on regions load without leaking raw transport text', async () => {
    mockAdmin.loadRegions.mockImplementation(async () => {
      mockAdmin.error = adminHttpErrorMessage(
        new Error('Network Error'),
        'Failed to load regions',
        'admin.galaxy.manage',
      );
    });

    const { rerender } = renderUniverse();
    await waitFor(() => expect(mockAdmin.loadRegions).toHaveBeenCalled());
    rerender(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(errorStripText()).toMatch(/Failed to load regions/i);
    });
    expect(errorStripText()).not.toMatch(/Network Error/i);
    expect(errorStripText()).not.toMatch(/TypeError/i);
    expect(screen.getByText('Andromeda Prime')).toBeTruthy();
  });

  it('collapses TypeError Failed to fetch on sectors load without leaking transport text', async () => {
    mockAdmin.loadSectors.mockImplementation(async () => {
      mockAdmin.error = adminHttpErrorMessage(
        new TypeError('Failed to fetch'),
        'Failed to load sectors',
        'admin.galaxy.manage',
      );
    });

    const { rerender } = renderUniverse();
    await waitFor(() => expect(mockAdmin.loadSectors).toHaveBeenCalled());
    rerender(
      <MemoryRouter>
        <UniverseManager />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(errorStripText()).toMatch(/Failed to load sectors/i);
    });
    expect(errorStripText()).not.toMatch(/Failed to fetch/i);
    expect(errorStripText()).not.toMatch(/TypeError/i);
    expect(screen.getByText('Andromeda Prime')).toBeTruthy();
  });
});
