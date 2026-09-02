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

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

const bareStatusError = (status: number) => {
  const err = new Error('');
  (err as { status?: number }).status = status;
  return err;
};

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

  it('surfaces 403 status path and preserves server detail (LEG-3828)', () => {
    expect(formatGovernanceLoadError(bareStatusError(403))).toBe(
      'You are not a member of this region.',
    );
    expect(formatGovernanceLoadError(apiRequestError(403, 'ERR_NOT_A_MEMBER'))).toBe(
      'ERR_NOT_A_MEMBER',
    );
  });

  it('surfaces 429 status path without raw status codes (LEG-3946)', () => {
    expect(formatGovernanceLoadError(bareStatusError(429))).toBe(
      'Governance lookup rate limit exceeded — wait a moment and try again.',
    );
    expect(formatGovernanceLoadError(bareStatusError(429))).not.toMatch(/\b429\b/);
    expect(formatGovernanceLoadError(bareStatusError(429))).not.toMatch(/HTTP 429/i);
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

  it('listElections 403 surfaces member copy without raw transport text (LEG-3946)', async () => {
    mockGetMyMembership.mockResolvedValue({ is_member: true, region_id: 'region-1' });
    mockListElections.mockRejectedValue(apiRequestError(403));
    mockListPolicies.mockResolvedValue([]);
    mockListTreaties.mockResolvedValue([]);

    await act(async () => {
      root.render(<GovernancePanel />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toMatch(/You are not a member of this region/i);
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('listPolicies 429 surfaces rate-limit copy without raw transport text (LEG-3946)', async () => {
    mockGetMyMembership.mockResolvedValue({ is_member: true, region_id: 'region-1' });
    mockListElections.mockResolvedValue([]);
    mockListPolicies.mockRejectedValue(apiRequestError(429));
    mockListTreaties.mockResolvedValue([]);

    await act(async () => {
      root.render(<GovernancePanel />);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toMatch(/Governance lookup rate limit exceeded/i);
    expect(container.textContent).not.toMatch(/\b429\b/);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
