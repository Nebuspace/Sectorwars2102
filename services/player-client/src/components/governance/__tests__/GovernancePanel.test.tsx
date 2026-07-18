// @vitest-environment jsdom
/**
 * GovernancePanel — regional-governance route shell (WO-REGOV-VOTE-UI).
 *
 * governanceAPI + useGame are mocked at the module boundary (not fetch);
 * GameLayout is stubbed as a passthrough (page chrome, out of scope here —
 * mirrors GalaxyMap.chart.test.tsx). Pins:
 *  - no current region -> a clean "No Region" empty state, zero API calls
 *  - a non-member (is_member: false) -> a clean "Not Yet a Citizen" empty
 *    state, and the list endpoints are never even called (they'd 403 anyway)
 *  - a member with zero elections -> a clean "No Elections" empty state on
 *    the default tab, not a blank screen or a crash
 *  - tab switching renders the policies list
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetMyMembership = vi.fn();
const mockListElections = vi.fn();
const mockListPolicies = vi.fn();
const mockListTreaties = vi.fn();

vi.mock('../../../services/api', () => ({
  governanceAPI: {
    getMyMembership: (...a: unknown[]) => mockGetMyMembership(...a),
    listElections: (...a: unknown[]) => mockListElections(...a),
    listPolicies: (...a: unknown[]) => mockListPolicies(...a),
    listTreaties: (...a: unknown[]) => mockListTreaties(...a),
  },
}));

let mockCurrentSector: { region_id: string | null; region_name?: string } | null = {
  region_id: 'region-1',
  region_name: 'Test Region',
};
let mockPlayerState: { id: string } | null = { id: 'player-c' };

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState, currentSector: mockCurrentSector }),
}));

vi.mock('../../layouts/GameLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import GovernancePanel from '../GovernancePanel';

const MEMBER_STATUS = {
  region_id: 'region-1',
  is_member: true,
  membership_type: 'citizen',
  stored_membership_type: 'citizen',
  owns_colony_in_region: false,
  can_vote: true,
  voting_power: 1,
  citizenship_source: 'membership',
};

const NON_MEMBER_STATUS = {
  ...MEMBER_STATUS,
  is_member: false,
  membership_type: null,
  can_vote: false,
  citizenship_source: null,
};

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('GovernancePanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    mockGetMyMembership.mockReset();
    mockListElections.mockReset();
    mockListPolicies.mockReset();
    mockListTreaties.mockReset();
    mockCurrentSector = { region_id: 'region-1', region_name: 'Test Region' };
    mockPlayerState = { id: 'player-c' };
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.clearAllMocks();
  });

  it('renders a clean "No Region" empty state and calls no APIs when there is no current region', async () => {
    mockCurrentSector = null;

    await act(async () => {
      root.render(<GovernancePanel />);
    });
    await flush();

    expect(container.textContent).toContain('No Region');
    expect(mockGetMyMembership).not.toHaveBeenCalled();
  });

  it('renders a clean non-citizen empty state and never calls the list endpoints for a non-member', async () => {
    mockGetMyMembership.mockResolvedValue(NON_MEMBER_STATUS);

    await act(async () => {
      root.render(<GovernancePanel />);
    });
    await flush();

    expect(container.textContent).toContain('Not Yet a Citizen');
    expect(mockListElections).not.toHaveBeenCalled();
    expect(mockListPolicies).not.toHaveBeenCalled();
    expect(mockListTreaties).not.toHaveBeenCalled();
  });

  it('renders a clean "No Elections" empty state for a member region with zero elections', async () => {
    mockGetMyMembership.mockResolvedValue(MEMBER_STATUS);
    mockListElections.mockResolvedValue([]);
    mockListPolicies.mockResolvedValue([]);
    mockListTreaties.mockResolvedValue([]);

    await act(async () => {
      root.render(<GovernancePanel />);
    });
    await flush();

    expect(container.textContent).toContain('No Elections');
    expect(container.querySelector('.gov-tabs')).not.toBeNull();
  });

  it('switches to the policies tab and renders a "+ PROPOSE POLICY" affordance for an eligible voter', async () => {
    mockGetMyMembership.mockResolvedValue(MEMBER_STATUS);
    mockListElections.mockResolvedValue([]);
    mockListPolicies.mockResolvedValue([]);
    mockListTreaties.mockResolvedValue([]);

    await act(async () => {
      root.render(<GovernancePanel />);
    });
    await flush();

    const tabs = Array.from(container.querySelectorAll('.gov-tabs button'));
    const policiesTab = tabs.find((b) => b.textContent === 'Policies') as HTMLButtonElement;
    await act(async () => { policiesTab.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(container.textContent).toContain('PROPOSE POLICY');
    expect(container.textContent).toContain('No Policies');
  });
});
