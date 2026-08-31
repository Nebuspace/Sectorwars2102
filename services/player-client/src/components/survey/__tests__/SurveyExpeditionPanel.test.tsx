// @vitest-environment jsdom
/**
 * SurveyExpeditionPanel (ADR-0091) — orchestrates orbital scan -> dispatch
 * expedition -> status/countdown while PENDING -> compare current vs
 * re-rolled -> settle (claim), with contest-window feedback for both the
 * win and CAS-loss case. OrbitalScanView/SiteGridPreview are mocked out
 * (each already has its own dedicated test file) so these tests isolate
 * this panel's own orchestration logic. Fake timers control the 5s status
 * poll and 1s countdown tick deterministically.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Expedition } from '../expeditionTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../OrbitalScanView', () => ({
  default: ({ planetId }: { planetId: string }) => <div data-testid="orbital-scan" data-planet-id={planetId} />,
}));
vi.mock('../SiteGridPreview', () => ({
  default: ({ result, fogged }: { result: unknown; fogged: boolean }) => (
    <div data-testid="site-grid" data-fogged={String(fogged)} data-has-result={String(result != null)} />
  ),
}));

const { mockList, mockGetStatus, mockLaunch, mockReroll, mockSettle } = vi.hoisted(() => ({
  mockList: vi.fn<() => Promise<{ expeditions: unknown[] }>>(async () => ({ expeditions: [] })),
  mockGetStatus: vi.fn<(id: string) => Promise<unknown>>(),
  mockLaunch: vi.fn<(planetId: string, shipId?: string) => Promise<unknown>>(),
  mockReroll: vi.fn<(id: string) => Promise<unknown>>(),
  mockSettle: vi.fn<(planetId: string) => Promise<void>>(async () => undefined),
}));

vi.mock('../../../services/api', () => ({
  expeditionAPI: {
    list: mockList,
    getStatus: mockGetStatus,
    launch: mockLaunch,
    reroll: mockReroll,
    settle: mockSettle,
  },
}));

import SurveyExpeditionPanel from '../SurveyExpeditionPanel';

const FIXED_NOW = new Date('2026-01-01T00:00:00Z').getTime();

const expedition = (overrides: Partial<Expedition> = {}): Expedition => ({
  id: 'exp-1',
  planet_id: 'planet-1',
  status: 'PENDING',
  result: null,
  launched_at: new Date(FIXED_NOW).toISOString(),
  ...overrides,
});

describe('SurveyExpeditionPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(FIXED_NOW);
    mockList.mockReset();
    mockList.mockResolvedValue({ expeditions: [] });
    mockGetStatus.mockReset();
    mockLaunch.mockReset();
    mockReroll.mockReset();
    mockSettle.mockReset();
    mockSettle.mockResolvedValue(undefined);
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

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const mount = async (props: Partial<React.ComponentProps<typeof SurveyExpeditionPanel>> = {}) => {
    await act(async () => {
      root.render(<SurveyExpeditionPanel planetId="planet-1" {...props} />);
    });
    await flush();
  };

  const launchBtn = () => container.querySelector('.survey-btn-primary') as HTMLButtonElement | null;
  const statusLabel = () => container.querySelector('.survey-status-label')?.textContent;
  const errorAlert = () => container.querySelector('.survey-error')?.textContent;

  describe('mount / restore', () => {
    it('shows the Launch Expedition button when there is no expedition on this planet', async () => {
      await mount();
      expect(launchBtn()?.textContent).toBe('Launch Expedition');
      expect(container.querySelector('.survey-status')).toBeNull();
    });

    it('restores the newest expedition for this planet, ignoring older ones and other planets', async () => {
      mockList.mockResolvedValue({
        expeditions: [
          expedition({ id: 'old', status: 'SUCCESS', launched_at: new Date(FIXED_NOW - 60_000).toISOString() }),
          expedition({ id: 'other-planet', planet_id: 'planet-9', status: 'FAILURE', launched_at: new Date(FIXED_NOW + 30_000).toISOString() }),
          expedition({ id: 'newest', status: 'FAILURE', launched_at: new Date(FIXED_NOW + 10_000).toISOString() }),
        ],
      });
      await mount();
      expect(statusLabel()).toBe('Expedition failed — no site found');
    });

    it('shows honest list-restore error and still allows launch when list() rejects', async () => {
      mockList.mockRejectedValue(new Error('network down'));
      await mount();
      expect(launchBtn()?.textContent).toBe('Launch Expedition');
      expect(container.querySelector('[data-testid="survey-list-error"]')?.textContent).toBe(
        'network down',
      );
    });
  });

  describe('launch', () => {
    it('calls expeditionAPI.launch with planetId + shipId and adopts the result as current', async () => {
      mockLaunch.mockResolvedValue(expedition({ id: 'exp-new' }));
      await mount({ shipId: 'ship-9' });

      await act(async () => {
        launchBtn()!.click();
      });
      await flush();

      expect(mockLaunch).toHaveBeenCalledWith('planet-1', 'ship-9');
      expect(statusLabel()).toBe('Expedition in progress…');
    });

    it('passes undefined for shipId when none is provided', async () => {
      mockLaunch.mockResolvedValue(expedition());
      await mount();
      await act(async () => {
        launchBtn()!.click();
      });
      await flush();
      expect(mockLaunch).toHaveBeenCalledWith('planet-1', undefined);
    });

    it('shows Launching… and a disabled button while the request is in flight', async () => {
      let resolveFn: (v: unknown) => void = () => {};
      mockLaunch.mockImplementation(() => new Promise((resolve) => { resolveFn = resolve; }));
      await mount();

      await act(async () => {
        launchBtn()!.click();
      });
      expect(launchBtn()?.textContent).toBe('Launching…');
      expect(launchBtn()?.disabled).toBe(true);

      await act(async () => {
        resolveFn(expedition());
      });
      await flush();
    });

    it('shows the error message when launch rejects with an Error', async () => {
      mockLaunch.mockRejectedValue(new Error('no ship available'));
      await mount();
      await act(async () => {
        launchBtn()!.click();
      });
      await flush();
      expect(errorAlert()).toBe('no ship available');
    });

    it('falls back to a generic message when launch rejects with no .message', async () => {
      mockLaunch.mockRejectedValue({});
      await mount();
      await act(async () => {
        launchBtn()!.click();
      });
      await flush();
      expect(errorAlert()).toBe('Failed to launch expedition');
    });
  });

  describe('PENDING countdown + polling', () => {
    it('renders a countdown that ticks down as time passes', async () => {
      mockList.mockResolvedValue({
        expeditions: [expedition({ launched_at: new Date(FIXED_NOW - 5 * 60_000).toISOString() })],
      });
      await mount();

      expect(container.querySelector('.survey-countdown')?.textContent).toBe('5:00');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(container.querySelector('.survey-countdown')?.textContent).toBe('4:59');
    });

    it('polls getStatus every 5s while PENDING and adopts a resolved result', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ id: 'poll-me' })] });
      mockGetStatus.mockResolvedValue(expedition({ id: 'poll-me', status: 'SUCCESS' }));
      await mount();

      expect(mockGetStatus).not.toHaveBeenCalled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(mockGetStatus).toHaveBeenCalledWith('poll-me');
      expect(statusLabel()).toBe('Expedition succeeded');

      // Once resolved, the poll effect's cleanup fires and no further calls happen.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000);
      });
      expect(mockGetStatus).toHaveBeenCalledTimes(1);
    });

    it('stays on the PENDING view without crashing when a poll tick rejects', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ id: 'flaky' })] });
      mockGetStatus.mockRejectedValue(new Error('transient'));
      await mount();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
      expect(statusLabel()).toBe('Expedition in progress…');
    });
  });

  describe.each([
    ['SUCCESS', 'Expedition succeeded'],
    ['PARTIAL', 'Partial intel recovered'],
    ['FAILURE', 'Expedition failed — no site found'],
  ] as const)('resolved status: %s', (status, label) => {
    it(`shows "${label}"`, async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status })] });
      await mount();
      expect(statusLabel()).toBe(label);
    });
  });

  describe('SiteGridPreview wiring', () => {
    it('does not render a site grid before any expedition exists', async () => {
      await mount();
      expect(container.querySelector('[data-testid="site-grid"]')).toBeNull();
    });

    it('renders a fogged, resultless grid while PENDING', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition()] });
      await mount();
      const grid = container.querySelector('[data-testid="site-grid"]');
      expect(grid?.getAttribute('data-fogged')).toBe('true');
      expect(grid?.getAttribute('data-has-result')).toBe('false');
    });

    it('renders an unfogged grid with the result on SUCCESS', async () => {
      mockList.mockResolvedValue({
        expeditions: [expedition({ status: 'SUCCESS', result: { shape_class: 'ridge' } })],
      });
      await mount();
      const grid = container.querySelector('[data-testid="site-grid"]');
      expect(grid?.getAttribute('data-fogged')).toBe('false');
      expect(grid?.getAttribute('data-has-result')).toBe('true');
    });

    it('renders no site grid at all on FAILURE', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'FAILURE' })] });
      await mount();
      expect(container.querySelector('[data-testid="site-grid"]')).toBeNull();
    });
  });

  describe('reroll', () => {
    it('shows the reroll button when resolved and not FAILURE', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'SUCCESS' })] });
      await mount();
      expect(container.querySelector('.survey-actions .survey-btn')?.textContent).toBe('Re-roll (new expedition)');
    });

    it('hides the reroll button on FAILURE', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'FAILURE' })] });
      await mount();
      expect(container.querySelector('.survey-actions')).toBeNull();
    });

    it('calls expeditionAPI.reroll and shows the compare view once it resolves', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ id: 'exp-1', status: 'SUCCESS', result: { shape_class: 'ridge', usable_slots: 4 } })] });
      mockReroll.mockResolvedValue(
        expedition({ id: 'exp-2', status: 'PARTIAL', result: { shape_class: 'valley', usable_slots: 2, banded: true } })
      );
      await mount();

      await act(async () => {
        (container.querySelector('.survey-actions .survey-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockReroll).toHaveBeenCalledWith('exp-1');
      expect(container.querySelector('.survey-compare')).not.toBeNull();
      const titles = Array.from(container.querySelectorAll('.survey-compare-card-title')).map((n) => n.textContent);
      expect(titles).toEqual(['Current', 'Re-rolled']);
    });

    it('shows an error and leaves rerolled unset when reroll rejects', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'SUCCESS' })] });
      mockReroll.mockRejectedValue(new Error('reroll unavailable'));
      await mount();

      await act(async () => {
        (container.querySelector('.survey-actions .survey-btn') as HTMLButtonElement).click();
      });
      await flush();

      expect(errorAlert()).toBe('reroll unavailable');
      expect(container.querySelector('.survey-compare')).toBeNull();
    });

    it('Keep Current dismisses the compare view without changing current', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ id: 'exp-1', status: 'SUCCESS' })] });
      mockReroll.mockResolvedValue(expedition({ id: 'exp-2', status: 'FAILURE' }));
      await mount();
      await act(async () => {
        (container.querySelector('.survey-actions .survey-btn') as HTMLButtonElement).click();
      });
      await flush();

      await act(async () => {
        (Array.from(container.querySelectorAll('.survey-compare-actions .survey-btn')).find(
          (b) => b.textContent === 'Keep Current'
        ) as HTMLButtonElement).click();
      });

      expect(container.querySelector('.survey-compare')).toBeNull();
      expect(statusLabel()).toBe('Expedition succeeded');
    });

    it('disables Accept Re-roll when the re-roll itself failed', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ id: 'exp-1', status: 'SUCCESS' })] });
      mockReroll.mockResolvedValue(expedition({ id: 'exp-2', status: 'FAILURE' }));
      await mount();
      await act(async () => {
        (container.querySelector('.survey-actions .survey-btn') as HTMLButtonElement).click();
      });
      await flush();

      const acceptBtn = Array.from(container.querySelectorAll('.survey-compare-actions .survey-btn')).find(
        (b) => b.textContent === 'Accept Re-roll'
      ) as HTMLButtonElement;
      expect(acceptBtn.disabled).toBe(true);
      expect(acceptBtn.title).toBe('A failed re-roll cannot be accepted');
    });

    it('Accept Re-roll adopts the re-rolled expedition as current', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ id: 'exp-1', status: 'SUCCESS' })] });
      mockReroll.mockResolvedValue(expedition({ id: 'exp-2', status: 'PARTIAL' }));
      await mount();
      await act(async () => {
        (container.querySelector('.survey-actions .survey-btn') as HTMLButtonElement).click();
      });
      await flush();

      await act(async () => {
        (Array.from(container.querySelectorAll('.survey-compare-actions .survey-btn')).find(
          (b) => b.textContent === 'Accept Re-roll'
        ) as HTMLButtonElement).click();
      });

      expect(container.querySelector('.survey-compare')).toBeNull();
      expect(statusLabel()).toBe('Partial intel recovered');
    });
  });

  describe('settle', () => {
    it('shows the settle button for PARTIAL', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'PARTIAL' })] });
      await mount();
      expect(container.querySelector('.survey-settle')).not.toBeNull();
    });

    it('hides the settle button on FAILURE', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'FAILURE' })] });
      await mount();
      expect(container.querySelector('.survey-settle')).toBeNull();
    });

    it('shows the won result on a successful settle and hides the settle button', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'SUCCESS' })] });
      await mount();

      await act(async () => {
        (container.querySelector('.survey-btn-settle') as HTMLButtonElement).click();
      });
      await flush();

      expect(mockSettle).toHaveBeenCalledWith('planet-1');
      expect(container.querySelector('.survey-settle-won')?.textContent).toContain('the colony is yours');
      expect(container.querySelector('.survey-settle')).toBeNull();
    });

    it('shows the lost result with the server message on a CAS-loss', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'SUCCESS' })] });
      mockSettle.mockRejectedValue(new Error('site already claimed'));
      await mount();

      await act(async () => {
        (container.querySelector('.survey-btn-settle') as HTMLButtonElement).click();
      });
      await flush();

      expect(container.querySelector('.survey-settle-lost')?.textContent).toContain('site already claimed');
    });

    it('falls back to a generic lost message when the rejection has no .message', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'SUCCESS' })] });
      mockSettle.mockRejectedValue({});
      await mount();

      await act(async () => {
        (container.querySelector('.survey-btn-settle') as HTMLButtonElement).click();
      });
      await flush();

      expect(container.querySelector('.survey-settle-lost')?.textContent).toContain(
        'Another expedition settled this site first.'
      );
    });

    it('shows Settling… and disables the button while the request is in flight', async () => {
      mockList.mockResolvedValue({ expeditions: [expedition({ status: 'SUCCESS' })] });
      let resolveFn: () => void = () => {};
      mockSettle.mockImplementation(() => new Promise((resolve) => { resolveFn = resolve as () => void; }));
      await mount();

      await act(async () => {
        (container.querySelector('.survey-btn-settle') as HTMLButtonElement).click();
      });
      const settleBtn = container.querySelector('.survey-btn-settle') as HTMLButtonElement;
      expect(settleBtn.textContent).toBe('Settling…');
      expect(settleBtn.disabled).toBe(true);

      await act(async () => {
        resolveFn();
      });
      await flush();
    });
  });

  describe('SiteCompareCard facts', () => {
    it('renders shape/slots/energy/native-life facts and the banded note when present', async () => {
      mockList.mockResolvedValue({
        expeditions: [
          expedition({
            id: 'exp-1',
            status: 'SUCCESS',
            result: { shape_class: 'ridge', usable_slots: 6, energy_baseline: 'moderate', native_life: true, banded: true },
          }),
        ],
      });
      mockReroll.mockResolvedValue(expedition({ id: 'exp-2', status: 'FAILURE', result: null }));
      await mount();
      await act(async () => {
        (container.querySelector('.survey-actions .survey-btn') as HTMLButtonElement).click();
      });
      await flush();

      const currentCard = container.querySelectorAll('.survey-compare-card')[0];
      const facts = Array.from(currentCard.querySelectorAll('.survey-compare-card-facts li')).map((li) => li.textContent);
      expect(facts).toEqual(['Shape: ridge', 'Slots: 6', 'Energy: moderate', 'Native life: Present', 'Banded intel — energy/resources unknown']);

      const rerolledCard = container.querySelectorAll('.survey-compare-card')[1];
      expect(rerolledCard.querySelector('.survey-compare-card-empty')?.textContent).toBe('No site data.');
    });
  });
});
