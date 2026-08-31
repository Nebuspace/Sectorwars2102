// @vitest-environment jsdom
/**
 * GovSummaryTab — the StatusBar dossier "GOVERNANCE" tab. Mirrors
 * TeamSummaryTab.test.tsx's seam (jsdom + react-dom/client createRoot +
 * act(), no RTL in this project).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetMyMembership = vi.fn();
const mockListElections = vi.fn();

vi.mock('../../services/api', () => ({
  governanceAPI: {
    getMyMembership: (...a: unknown[]) => mockGetMyMembership(...a),
    listElections: (...a: unknown[]) => mockListElections(...a),
  },
}));

let mockCurrentSector: { region_id?: string } | null = null;

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({ currentSector: mockCurrentSector }),
}));

import GovSummaryTab, { formatGovSummaryLoadError } from './GovSummaryTab';

const MEMBER_RESPONSE = {
  region_id: 'region-1',
  is_member: true,
  membership_type: 'citizen',
  stored_membership_type: 'citizen',
  owns_colony_in_region: true,
  can_vote: true,
  voting_power: 1,
  citizenship_source: 'colony',
};

const NON_MEMBER_RESPONSE = {
  region_id: 'region-1',
  is_member: false,
  membership_type: null,
  stored_membership_type: null,
  owns_colony_in_region: false,
  can_vote: false,
  voting_power: 0,
  citizenship_source: null,
};

const ELECTIONS_RESPONSE = [
  {
    id: 'elec-1',
    position: 'Governor',
    candidates: [],
    voting_opens_at: '2026-09-01T00:00:00Z',
    voting_closes_at: '2026-09-08T00:00:00Z',
    results: null,
    status: 'pending',
  },
];

describe('GovSummaryTab', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetMyMembership.mockReset();
    mockListElections.mockReset();
    mockCurrentSector = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <GovSummaryTab />
        </MemoryRouter>
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('renders the No Region empty state with zero API calls when there is no region_id', async () => {
    mockCurrentSector = { region_id: undefined };
    await mount();

    expect(container.querySelector('.sb-gov-error')).toBeNull();
    expect(container.textContent).toContain('No Region');
    expect(mockGetMyMembership).not.toHaveBeenCalled();
    expect(mockListElections).not.toHaveBeenCalled();
  });

  it('renders standing, vote eligibility, and next election from real membership + election data', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockResolvedValue(MEMBER_RESPONSE);
    mockListElections.mockResolvedValue(ELECTIONS_RESPONSE);
    await mount();

    expect(mockGetMyMembership).toHaveBeenCalledWith('region-1');
    expect(mockListElections).toHaveBeenCalledWith('region-1');
    const nameEl = container.querySelector('.sb-gov-name');
    expect(nameEl?.textContent).toBe('Regional Governance');
    expect(nameEl?.tagName).toBe('H2');

    const values = Array.from(container.querySelectorAll('.sb-identity-v')).map((el) => el.textContent);
    expect(values).toContain('Citizen');
    expect(values).toContain('Yes');
    expect(values).toContain('Governor');
  });

  it('renders the Not Yet a Citizen empty state and skips the elections call for a non-member', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockResolvedValue(NON_MEMBER_RESPONSE);
    await mount();

    expect(container.textContent).toContain('Not Yet a Citizen');
    expect(mockListElections).not.toHaveBeenCalled();
  });

  it('shows an error state instead of crashing when the fetch fails', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockRejectedValue(new Error('Network down'));
    await mount();

    const errorEl = container.querySelector('.sb-gov-error');
    expect(errorEl?.textContent).toBe('Network down');
    expect(errorEl?.getAttribute('role')).toBe('alert');
  });

  it('surfaces load 403 ERR_NOT_A_MEMBER server detail', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockRejectedValue(
      Object.assign(new Error('ERR_NOT_A_MEMBER'), { status: 403 }),
    );
    await mount();

    expect(container.querySelector('.sb-gov-error')?.textContent).toBe('ERR_NOT_A_MEMBER');
  });

  it('surfaces load 404 region-not-found server detail', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockRejectedValue(
      Object.assign(new Error('Region not found'), { status: 404 }),
    );
    await mount();

    expect(container.querySelector('.sb-gov-error')?.textContent).toBe('Region not found');
  });

  it('formatGovSummaryLoadError falls back on bare 403 without server detail', () => {
    const err = Object.assign(new Error('API Error: 403'), { status: 403 });
    expect(formatGovSummaryLoadError(err)).toBe('You are not a member of this region.');
  });

  it('formatGovSummaryLoadError falls back on bare 404 without server detail', () => {
    const err = Object.assign(new Error('API Error: 404'), { status: 404 });
    expect(formatGovSummaryLoadError(err)).toBe('Region not found.');
  });

  it('formatGovSummaryLoadError falls back on TypeError network collapse (LEG-3091)', () => {
    const text = formatGovSummaryLoadError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Failed to load governance data/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces honest load fallback when getMyMembership rejects with TypeError', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockRejectedValue(new TypeError('Failed to fetch'));
    await mount();

    const errorEl = container.querySelector('.sb-gov-error');
    expect(errorEl?.textContent).toMatch(/Failed to load governance data/i);
    expect(errorEl?.textContent).not.toMatch(/Failed to fetch/i);
    expect(errorEl?.textContent).not.toMatch(/TypeError/i);
    expect(errorEl?.getAttribute('role')).toBe('alert');
  });

  it('does not crash on a fully malformed (empty object) membership response', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockResolvedValue({});

    await expect(mount()).resolves.not.toThrow();

    expect(container.querySelector('.sb-gov-error')).toBeNull();
    expect(container.textContent).toContain('Not Yet a Citizen');
    expect(mockListElections).not.toHaveBeenCalled();
  });

  it('does not crash when listElections returns a malformed (empty object) payload', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    mockGetMyMembership.mockResolvedValue(MEMBER_RESPONSE);
    mockListElections.mockResolvedValue({});

    await expect(mount()).resolves.not.toThrow();

    expect(container.querySelector('.sb-gov-error')).toBeNull();
    expect(container.textContent).toContain('Regional Governance');
    expect(container.textContent).toContain('None scheduled');
  });

  it('shows a status/aria-live loading state before the fetch resolves', async () => {
    mockCurrentSector = { region_id: 'region-1' };
    let resolveFn: (v: unknown) => void = () => {};
    mockGetMyMembership.mockReturnValue(new Promise((r) => { resolveFn = r; }));

    await act(async () => {
      root.render(
        <MemoryRouter>
          <GovSummaryTab />
        </MemoryRouter>
      );
    });

    const loadingEl = container.querySelector('.sb-gov-loading');
    expect(loadingEl?.textContent).toBe('Loading…');
    expect(loadingEl?.getAttribute('role')).toBe('status');
    expect(loadingEl?.getAttribute('aria-live')).toBe('polite');

    await act(async () => {
      resolveFn(MEMBER_RESPONSE);
    });
  });
});
