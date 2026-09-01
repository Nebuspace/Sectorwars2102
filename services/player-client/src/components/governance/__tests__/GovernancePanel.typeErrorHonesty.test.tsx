// @vitest-environment jsdom
/**
 * LEG-3603 Soft-ORDER — GovernancePanel Network Error densify Vitest.
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
    getMyMembership: (...args: unknown[]) => mockGetMyMembership(...args),
    listElections: (...args: unknown[]) => mockListElections(...args),
    listPolicies: (...args: unknown[]) => mockListPolicies(...args),
    listTreaties: (...args: unknown[]) => mockListTreaties(...args),
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

import GovernancePanel, { formatGovernanceLoadError } from '../GovernancePanel';

describe('formatGovernanceLoadError Network Error densify (LEG-3603)', () => {
  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatGovernanceLoadError(new Error('Network Error'))).toBe(
      'Failed to load regional governance.',
    );
    expect(formatGovernanceLoadError(new Error('Failed to fetch'))).toBe(
      'Failed to load regional governance.',
    );
    expect(formatGovernanceLoadError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves structured API detail when not transport collapse', () => {
    expect(formatGovernanceLoadError(new Error('region_governance_disabled'))).toBe(
      'region_governance_disabled',
    );
  });
});

describe('GovernancePanel initial load Network Error densify (LEG-3603)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockCurrentSector = { region_id: 'region-1', region_name: 'Test Region' };
    mockPlayerState = { id: 'player-c' };
    mockGetMyMembership.mockReset();
    mockListElections.mockReset();
    mockListPolicies.mockReset();
    mockListTreaties.mockReset();
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

  it('membership load Network Error surfaces honest fallback without raw transport text', async () => {
    mockGetMyMembership.mockRejectedValue(new Error('Network Error'));

    await act(async () => {
      root.render(<GovernancePanel />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toMatch(/Failed to load regional governance/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
