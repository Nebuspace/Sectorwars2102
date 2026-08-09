// @vitest-environment jsdom
/**
 * RegionTreatyPanel — accept/reject/terminate wiring
 * (WO-ESCALATE-REGIONAL-TREATY-FLOW-PRIORITY).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockListMyTreaties,
  mockAcceptTreaty,
  mockRejectTreaty,
  mockTerminateTreaty,
  mockProposeTreaty,
} = vi.hoisted(() => ({
  mockListMyTreaties: vi.fn(),
  mockAcceptTreaty: vi.fn(),
  mockRejectTreaty: vi.fn(),
  mockTerminateTreaty: vi.fn(),
  mockProposeTreaty: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  regionOwnerAPI: {
    listMyTreaties: (...a: unknown[]) => mockListMyTreaties(...a),
    acceptTreaty: (...a: unknown[]) => mockAcceptTreaty(...a),
    rejectTreaty: (...a: unknown[]) => mockRejectTreaty(...a),
    terminateTreaty: (...a: unknown[]) => mockTerminateTreaty(...a),
    proposeTreaty: (...a: unknown[]) => mockProposeTreaty(...a),
  },
}));

import RegionTreatyPanel from '../RegionTreatyPanel';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('RegionTreatyPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockListMyTreaties.mockReset();
    mockAcceptTreaty.mockReset();
    mockRejectTreaty.mockReset();
    mockTerminateTreaty.mockReset();
    mockProposeTreaty.mockReset();
    mockListMyTreaties.mockResolvedValue([
      {
        id: 't-pending',
        region_a_name: 'Alpha',
        region_b_name: 'Beta',
        treaty_type: 'trade',
        status: 'proposed',
      },
      {
        id: 't-active',
        region_a_name: 'Alpha',
        region_b_name: 'Gamma',
        treaty_type: 'defense',
        status: 'active',
      },
    ]);
    mockAcceptTreaty.mockResolvedValue({ message: 'ok' });
    mockRejectTreaty.mockResolvedValue({ message: 'ok' });
    mockTerminateTreaty.mockResolvedValue({ message: 'ok' });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const mount = async () => {
    await act(async () => {
      root.render(
        <RegionTreatyPanel regionId="reg-1" regionName="Beta" onClose={() => undefined} />,
      );
      await flush();
      await flush();
    });
  };

  it('lists pending + active and routes Accept / Terminate through regionOwnerAPI', async () => {
    await mount();
    expect(mockListMyTreaties).toHaveBeenCalledWith('reg-1');
    expect(container.textContent).toContain('PENDING (1)');
    expect(container.textContent).toContain('ACTIVE (1)');

    const accept = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'ACCEPT',
    );
    expect(accept).toBeTruthy();
    mockListMyTreaties.mockResolvedValueOnce([
      {
        id: 't-active',
        region_a_name: 'Alpha',
        region_b_name: 'Gamma',
        treaty_type: 'defense',
        status: 'active',
      },
    ]);
    await act(async () => {
      accept!.click();
      await flush();
      await flush();
    });
    expect(mockAcceptTreaty).toHaveBeenCalledWith('t-pending');

    const terminate = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'TERMINATE',
    );
    await act(async () => {
      terminate!.click();
      await flush();
      await flush();
    });
    expect(mockTerminateTreaty).toHaveBeenCalledWith('t-active');
  });
});
