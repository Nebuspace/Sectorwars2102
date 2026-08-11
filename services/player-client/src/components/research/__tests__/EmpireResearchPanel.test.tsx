// @vitest-environment jsdom
/**
 * EmpireResearchPanel — CRT-T1.5-9/CRT-4 empire research cockpit (§5.4/§5.5/
 * §5.7 + the tech tree). jsdom + react-dom/client createRoot() + act(), no
 * RTL. researchCockpitAPI and useWebSocket mocked; fake timers pin
 * Date.now() to drive the "perishes in" countdown and auto-hide of expired
 * offers deterministically.
 *
 * Pins: the resilient Promise.allSettled fetch (a failed offers read doesn't
 * blank a successful cockpit read, and vice versa), the tapering/throughput
 * copy + lastGovernorStatus-over-cockpit precedence, '—' fallbacks for null
 * summary fields, the tech tree's locked/affordable/unlocked/expensive
 * per-node state + its unlock-button disabled/title reasons, the
 * researchEventSignal-driven silent refetch (no spinner), offer accept/
 * ignore (ignore is purely local, no API call), the expired-offer auto-hide
 * via visibleOffers, and the global disable of all accept/unlock buttons
 * while any single action is in flight.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetCockpit, mockGetOffers, mockStartContract, mockUnlockNode } = vi.hoisted(() => ({
  mockGetCockpit: vi.fn(),
  mockGetOffers: vi.fn(),
  mockStartContract: vi.fn(),
  mockUnlockNode: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  researchCockpitAPI: {
    getCockpit: mockGetCockpit,
    getOffers: mockGetOffers,
    startContract: mockStartContract,
    unlockNode: mockUnlockNode,
  },
}));

let mockResearchEventSignal = 0;
let mockLastGovernorStatus: { rpPerDay: number | null; throughputPct: number | null; ariaText: string | null } | null = null;
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    researchEventSignal: mockResearchEventSignal,
    lastGovernorStatus: mockLastGovernorStatus,
  }),
}));

import EmpireResearchPanel from '../EmpireResearchPanel';

const NOW = Date.parse('2026-08-09T12:00:00Z');

const cockpit = (overrides: Record<string, unknown> = {}) => ({
  rpPerDay: 1200,
  rpThroughputPct: 100,
  banked: 5000,
  spent: 20000,
  contractsActive: 2,
  worldsFrontier: 3,
  worldsDone: 1,
  governorHeadroom: 0,
  techTree: [],
  ...overrides,
});

const techNode = (overrides: Record<string, unknown> = {}) => ({
  id: 'node-1',
  name: 'Rail Gun',
  branch: 'defense',
  tier: 2,
  rpCost: 800,
  prereqs: [],
  unlocked: false,
  prereqsMet: true,
  affordable: true,
  ...overrides,
});

const offer = (overrides: Record<string, unknown> = {}) => ({
  id: 'offer-1',
  kind: 'overclock',
  planetId: 'planet-1',
  planetName: 'New Earth',
  rpCost: 100,
  crCost: 5000,
  magnitude: 2,
  expiresAt: null,
  ...overrides,
});

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('EmpireResearchPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    mockResearchEventSignal = 0;
    mockLastGovernorStatus = null;

    mockGetCockpit.mockReset();
    mockGetCockpit.mockResolvedValue(cockpit());
    mockGetOffers.mockReset();
    mockGetOffers.mockResolvedValue({ offers: [] });
    mockStartContract.mockReset();
    mockUnlockNode.mockReset();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<EmpireResearchPanel />);
    });
    await flush();
  };

  it('shows the loading telemetry message before the first fetch resolves', async () => {
    let resolveCockpit: (v: unknown) => void = () => {};
    mockGetCockpit.mockReturnValue(new Promise((resolve) => { resolveCockpit = resolve; }));
    await act(async () => {
      root.render(<EmpireResearchPanel />);
    });
    expect(container.textContent).toContain('Reading empire research telemetry...');
    await act(async () => {
      resolveCockpit(cockpit());
    });
    await flush();
    expect(container.querySelector('.er-header')).not.toBeNull();
  });

  it('shows a load-error with Retry when the cockpit fetch fails on initial load', async () => {
    mockGetCockpit.mockRejectedValue(new Error('research service unreachable'));
    await mount();
    expect(container.querySelector('.empire-research-error')?.textContent).toContain('research service unreachable');

    mockGetCockpit.mockResolvedValue(cockpit());
    await act(async () => {
      (container.querySelector('.er-retry-btn') as HTMLButtonElement).click();
    });
    await flush();
    expect(container.querySelector('.er-header')).not.toBeNull();
  });

  it('renders the cockpit even when only the offers read fails (resilient allSettled)', async () => {
    mockGetOffers.mockRejectedValue(new Error('offers unreachable'));
    await mount();
    expect(container.querySelector('.empire-research-error')).toBeNull();
    expect(container.querySelector('.er-offers-empty')).not.toBeNull();
  });

  describe('headroom readout', () => {
    it('shows full-throughput copy (not tapering) at >=100%', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ rpThroughputPct: 100 }));
      await mount();
      expect(container.querySelector('.er-headroom')?.className).not.toContain('tapering');
      expect(container.querySelector('.er-headroom-copy')?.textContent).toContain('At full throughput');
    });

    it('shows tapering copy + styling below 100%', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ rpThroughputPct: 62 }));
      await mount();
      expect(container.querySelector('.er-headroom')?.className).toContain('tapering');
      expect(container.querySelector('.er-throughput')?.textContent).toBe('throughput 62%');
      expect(container.querySelector('.er-headroom-copy')?.textContent).toContain('Past full throughput');
    });

    it('prefers lastGovernorStatus.throughputPct over the cockpit read when present', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ rpThroughputPct: 100 }));
      mockLastGovernorStatus = { rpPerDay: 1500, throughputPct: 40, ariaText: null };
      await mount();
      expect(container.querySelector('.er-throughput')?.textContent).toBe('throughput 40%');
      expect(container.querySelector('.er-headroom')?.className).toContain('tapering');
    });

    it('shows the capstone-headroom note only when governorHeadroom > 0', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ governorHeadroom: 0 }));
      await mount();
      expect(container.querySelector('.er-headroom-unlock')).toBeNull();

      mockGetCockpit.mockResolvedValue(cockpit({ governorHeadroom: 300 }));
      await act(async () => { root.unmount(); });
      root = createRoot(container);
      await mount();
      expect(container.querySelector('.er-headroom-unlock')?.textContent).toContain('+300 RP/day');
    });
  });

  describe('R&D summary', () => {
    it('renders all four summary rows with compact formatting', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ rpPerDay: 1_200_000, banked: 5000, spent: 20000, contractsActive: 2, worldsFrontier: 3, worldsDone: 1 }));
      await mount();
      const rows = Array.from(container.querySelectorAll('.er-summary-row .er-row-value')).map((n) => n.textContent);
      expect(rows[0]).toBe('1.2M');
      expect(rows[1]).toBe('20k / 5k');
      expect(rows[2]).toBe('2');
      expect(rows[3]).toBe('3 / 1');
    });

    it('falls back to em-dash for null summary fields', async () => {
      mockGetCockpit.mockResolvedValue({ techTree: [] });
      await mount();
      const rows = Array.from(container.querySelectorAll('.er-summary-row .er-row-value')).map((n) => n.textContent);
      expect(rows[0]).toBe('—');
      expect(rows[1]).toBe('— / —');
      expect(rows[2]).toBe('—');
      expect(rows[3]).toBe('— / —');
    });
  });

  describe('tech tree', () => {
    it('shows the empty-catalog message when techTree is empty', async () => {
      await mount();
      expect(container.querySelector('.er-tech-empty')?.textContent).toBe('No tech nodes in the catalog.');
    });

    it('renders the unlocked count, and per-node state classes', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({
        techTree: [
          techNode({ id: 'a', unlocked: true }),
          techNode({ id: 'b', unlocked: false, prereqsMet: false }),
          techNode({ id: 'c', unlocked: false, prereqsMet: true, affordable: false }),
          techNode({ id: 'd', unlocked: false, prereqsMet: true, affordable: true }),
        ],
      }));
      await mount();
      expect(container.querySelector('.er-tech-count')?.textContent).toBe('1/4');
      const nodes = Array.from(container.querySelectorAll('.er-tech-node'));
      expect(nodes[0].className).toContain('er-tech-node-unlocked');
      expect(nodes[1].className).toContain('er-tech-node-locked');
      expect(nodes[1].textContent).toContain('requires a prerequisite node');
      expect(nodes[2].className).toContain('er-tech-node-expensive');
      expect(nodes[2].textContent).toContain('insufficient banked RP');
      expect(nodes[3].className).toContain('er-tech-node-affordable');
    });

    it('unlocks a node, shows the success message, and refetches without a spinner flash', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ techTree: [techNode()] }));
      mockUnlockNode.mockResolvedValue({});
      await mount();
      await act(async () => {
        (container.querySelector('.unlock-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockUnlockNode).toHaveBeenCalledWith('node-1');
      expect(container.querySelector('.er-message.ok')?.textContent).toBe('Unlocked Rail Gun.');
      expect(container.querySelector('.empire-research-loading')).toBeNull();
    });

    it('shows an error message when unlocking fails', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ techTree: [techNode()] }));
      mockUnlockNode.mockRejectedValue(new Error('insufficient banked RP'));
      await mount();
      await act(async () => {
        (container.querySelector('.unlock-btn') as HTMLButtonElement).click();
      });
      await flush();
      expect(container.querySelector('.er-message.err')?.textContent).toBe('insufficient banked RP');
    });

    it('disables the unlock button when not affordable, with the not-enough-RP title', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ techTree: [techNode({ affordable: false })] }));
      await mount();
      const btn = container.querySelector('.unlock-btn') as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(btn.title).toBe('Not enough banked RP yet');
    });
  });

  describe('offers', () => {
    it('shows the no-directives message when there are none', async () => {
      await mount();
      expect(container.querySelector('.er-offers-empty')?.textContent).toContain('running clean');
    });

    it('renders kind label/blurb and conditional cost fields', async () => {
      mockGetOffers.mockResolvedValue({ offers: [offer()] });
      await mount();
      const off = container.querySelector('.er-offer') as HTMLElement;
      expect(off.querySelector('.er-offer-kind')?.textContent).toBe('Overclock');
      expect(off.querySelector('.er-offer-where')?.textContent).toBe('New Earth');
      expect(off.querySelector('.er-offer-blurb')?.textContent).toContain('Push a world past its rated output');
      expect(off.querySelector('.er-cost-cr')?.textContent).toContain('5k');
      expect(off.querySelector('.er-cost-rp')?.textContent).toContain('100');
      expect(off.querySelector('.er-cost-mag')?.textContent).toContain('2');
    });

    it('shows a live "perishes in" countdown that updates as time advances', async () => {
      mockGetOffers.mockResolvedValue({ offers: [offer({ expiresAt: new Date(NOW + 90 * 60_000).toISOString() })] });
      await mount();
      expect(container.querySelector('.er-offer-perish')?.textContent).toBe('⏳ perishes in 1h 30m');
      await act(async () => {
        vi.advanceTimersByTime(31 * 60_000);
      });
      expect(container.querySelector('.er-offer-perish')?.textContent).toBe('⏳ perishes in 59m');
    });

    it('auto-hides an offer once its expiry passes', async () => {
      mockGetOffers.mockResolvedValue({ offers: [offer({ expiresAt: new Date(NOW + 5000).toISOString() })] });
      await mount();
      expect(container.querySelector('.er-offer')).not.toBeNull();
      await act(async () => {
        vi.advanceTimersByTime(6000);
      });
      expect(container.querySelector('.er-offer')).toBeNull();
      expect(container.querySelector('.er-offers-empty')).not.toBeNull();
    });

    it('accepts an offer, shows the success message with the planet name, and refetches', async () => {
      mockGetOffers.mockResolvedValue({ offers: [offer()] });
      mockStartContract.mockResolvedValue({});
      await mount();
      await act(async () => {
        (container.querySelector('.accept-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockStartContract).toHaveBeenCalledWith({ offerId: 'offer-1', planetId: 'planet-1' });
      expect(container.querySelector('.er-message.ok')?.textContent).toBe('Overclock started on New Earth.');
    });

    it('shows an error message when accept fails', async () => {
      mockGetOffers.mockResolvedValue({ offers: [offer()] });
      mockStartContract.mockRejectedValue(new Error('insufficient credits'));
      await mount();
      await act(async () => {
        (container.querySelector('.accept-btn') as HTMLButtonElement).click();
      });
      await flush();
      expect(container.querySelector('.er-message.err')?.textContent).toBe('insufficient credits');
    });

    it('ignoring an offer removes it locally without calling the API', async () => {
      mockGetOffers.mockResolvedValue({ offers: [offer()] });
      await mount();
      await act(async () => {
        (container.querySelector('.ignore-btn') as HTMLButtonElement).click();
      });
      expect(container.querySelector('.er-offer')).toBeNull();
      expect(mockStartContract).not.toHaveBeenCalled();
    });

    it('disables all accept/unlock buttons while one action is in flight', async () => {
      mockGetCockpit.mockResolvedValue(cockpit({ techTree: [techNode({ id: 'node-a' })] }));
      mockGetOffers.mockResolvedValue({ offers: [offer({ id: 'off-a' }), offer({ id: 'off-b' })] });
      let resolveAccept: (v: unknown) => void = () => {};
      mockStartContract.mockReturnValue(new Promise((resolve) => { resolveAccept = resolve; }));
      await mount();

      const acceptButtons = Array.from(container.querySelectorAll('.accept-btn')) as HTMLButtonElement[];
      await act(async () => { acceptButtons[0].click(); });
      expect(acceptButtons[1].disabled).toBe(true);
      expect((container.querySelector('.unlock-btn') as HTMLButtonElement).disabled).toBe(true);

      await act(async () => { resolveAccept({}); });
      await flush();
    });
  });

  it('silently refetches (no loading spinner) when researchEventSignal changes', async () => {
    await mount();
    mockGetCockpit.mockClear();
    mockResearchEventSignal = 1;
    await act(async () => {
      root.render(<EmpireResearchPanel />);
    });
    await flush();
    expect(mockGetCockpit).toHaveBeenCalled();
    expect(container.querySelector('.empire-research-loading')).toBeNull();
  });
});
