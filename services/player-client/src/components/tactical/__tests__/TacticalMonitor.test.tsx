// @vitest-environment jsdom
/**
 * TacticalMonitor — the cockpit TACTICAL deck-monitor (WO-UI2-TACTICAL-
 * MONITOR).
 *
 * Mirrors ThreatPage.test.tsx / DeckPageTabs.test.tsx's harness (jsdom +
 * react-dom/client createRoot + act(), no RTL -- this repo has no
 * @testing-library/react dependency and no test file uses it; this is the
 * established pattern this codebase actually proves components with).
 *
 * Covers: band -> color+text mapping for all 4 bands (incl. color-not-alone
 * a11y), contributor expand/collapse, the two degraded-feed shapes (500 /
 * empty array), and current-sector prominence (band+score+live readout
 * pinned regardless of scroll).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Same jsdom+createRoot+act harness quirk noted in StatusBar.smoke.test.tsx
// / DeckPageTabs.test.tsx -- baseline-wide in this repo, unrelated to
// TacticalMonitor itself.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGetThreat = vi.fn();

vi.mock('../../../services/api', () => ({
  navAPI: {
    getThreat: (...a: unknown[]) => mockGetThreat(...a),
  },
}));

import TacticalMonitor from '../TacticalMonitor';

describe('TacticalMonitor', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetThreat.mockReset();
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

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  const mount = async (props: Partial<React.ComponentProps<typeof TacticalMonitor>> = {}) => {
    const currentSectorId = 'currentSectorId' in props ? props.currentSectorId : 5;
    await act(async () => {
      root.render(
        <TacticalMonitor
          currentSectorId={currentSectorId}
          currentSectorName={props.currentSectorName}
          liveShipCount={props.liveShipCount ?? 0}
        />
      );
    });
    await flush();
  };

  // -------------------------------------------------------------------
  // Band -> color+text mapping (all 4 bands, color-not-alone a11y)
  // -------------------------------------------------------------------

  it('renders each band as a chip carrying the band NAME as text (not color alone), with the STATUS TRIAD colors', async () => {
    mockGetThreat.mockResolvedValue([
      { sector_id: 1, score: 2, band: 'CLEAR', contributors: [] },
      { sector_id: 2, score: 30, band: 'CAUTION', contributors: [] },
      { sector_id: 3, score: 60, band: 'HOSTILE', contributors: [] },
      { sector_id: 4, score: 86, band: 'LETHAL', contributors: [] },
    ]);

    await mount({ currentSectorId: 999 }); // not in the rollup -- keeps the list untouched by "(CURRENT)"

    const rows = Array.from(container.querySelectorAll('.tactical-sector-row'));
    expect(rows).toHaveLength(4);

    const chipFor = (sectorId: number) => {
      const row = rows.find((r) => r.querySelector('.tactical-sector-id')?.textContent?.includes(`SECTOR ${sectorId}`));
      return row?.querySelector('.tactical-band-chip') ?? null;
    };

    const clear = chipFor(1)!;
    expect(clear.textContent).toContain('CLEAR');
    expect(clear.className).toContain('tactical-band-clear');

    const caution = chipFor(2)!;
    expect(caution.textContent).toContain('CAUTION');
    expect(caution.className).toContain('tactical-band-caution');

    const hostile = chipFor(3)!;
    expect(hostile.textContent).toContain('HOSTILE');
    expect(hostile.className).toContain('tactical-band-danger');

    const lethal = chipFor(4)!;
    expect(lethal.textContent).toContain('LETHAL');
    expect(lethal.className).toContain('tactical-band-danger');

    // HOSTILE and LETHAL intentionally share the same red tier (className) --
    // the TEXT is the only thing distinguishing them, proving color alone
    // is never the sole channel (WCAG).
    expect(hostile.className).toBe(lethal.className);
    expect(hostile.textContent).not.toBe(lethal.textContent);
  });

  it('sorts the known-sector list most-dangerous-first', async () => {
    mockGetThreat.mockResolvedValue([
      { sector_id: 10, score: 5, band: 'CLEAR', contributors: [] },
      { sector_id: 11, score: 70, band: 'HOSTILE', contributors: [] },
      { sector_id: 12, score: 40, band: 'CAUTION', contributors: [] },
    ]);

    await mount({ currentSectorId: 999 });

    const ids = Array.from(container.querySelectorAll('.tactical-sector-id')).map((el) => el.textContent);
    expect(ids).toEqual(['SECTOR 11', 'SECTOR 12', 'SECTOR 10']);
  });

  // -------------------------------------------------------------------
  // Contributor expand
  // -------------------------------------------------------------------

  it('expands a sector row to show its top contributors, and collapses on a second click', async () => {
    mockGetThreat.mockResolvedValue([
      {
        sector_id: 7,
        score: 42,
        band: 'HOSTILE',
        contributors: [
          { input: 'low_security', points: 28 },
          { input: 'hazard', points: 14 },
        ],
      },
    ]);

    await mount({ currentSectorId: 999 });

    expect(container.querySelector('.tactical-contributors')).toBeNull();

    const summary = container.querySelector('.tactical-sector-summary')!;
    // aria-controls is present even collapsed (points at the panel id the
    // button will disclose) and matches the contributors panel's id once
    // expanded -- completes the disclosure-widget contract alongside
    // aria-expanded (Pixel a11y gate, WO-UI2-TACTICAL-MONITOR REVISE).
    expect(summary.getAttribute('aria-controls')).toBe('tactical-contributors-7');

    await click(summary);

    const contributors = container.querySelector('.tactical-contributors');
    expect(contributors).not.toBeNull();
    expect(contributors!.id).toBe('tactical-contributors-7');
    expect(summary.getAttribute('aria-controls')).toBe(contributors!.id);
    expect(contributors!.textContent).toContain('low_security 28');
    expect(contributors!.textContent).toContain('hazard 14');
    expect(summary.getAttribute('aria-expanded')).toBe('true');

    await click(summary);
    expect(container.querySelector('.tactical-contributors')).toBeNull();
    expect(summary.getAttribute('aria-expanded')).toBe('false');
  });

  // -------------------------------------------------------------------
  // Degraded feed
  // -------------------------------------------------------------------

  it('a failed threat fetch (500) shows a graceful empty-state, not a crash', async () => {
    mockGetThreat.mockRejectedValue(new Error('Internal Server Error'));

    await mount();

    expect(container.querySelector('.tactical-sector-list .empty-state')?.textContent).toContain(
      'TACTICAL FEED UNAVAILABLE'
    );
    // The current-sector chip degrades too, rather than showing stale/fabricated data.
    expect(container.querySelector('.tactical-band-chip')?.textContent).toContain('FEED DOWN');
  });

  it('an empty known-sector array shows a graceful empty-state, not a crash', async () => {
    mockGetThreat.mockResolvedValue([]);

    await mount();

    const empty = container.querySelector('.tactical-sector-list .empty-state');
    expect(empty?.textContent).toContain('NO KNOWN SECTORS CHARTED');
    // Degraded/loading/empty states announce to assistive tech (Pixel a11y
    // gate, WO-UI2-TACTICAL-MONITOR REVISE) -- role=status lives on the
    // empty-state div itself, never on the role="list" container.
    expect(empty?.getAttribute('role')).toBe('status');
    expect(empty?.getAttribute('aria-live')).toBe('polite');
    expect(container.querySelector('.tactical-sector-list')?.getAttribute('role')).toBe('list');
  });

  it('no current sector telemetry (undefined id) shows its own empty-state without crashing', async () => {
    mockGetThreat.mockResolvedValue([]);

    await mount({ currentSectorId: undefined });

    expect(container.querySelector('.tactical-current-section .empty-state')?.textContent).toBe(
      'NO SECTOR TELEMETRY'
    );
  });

  // -------------------------------------------------------------------
  // Current-sector prominence
  // -------------------------------------------------------------------

  it('surfaces the current sector\'s band+score prominently plus the live ship-count readout', async () => {
    mockGetThreat.mockResolvedValue([
      { sector_id: 5, score: 55, band: 'HOSTILE', contributors: [{ input: 'pirate_pressure', points: 40 }] },
      { sector_id: 6, score: 3, band: 'CLEAR', contributors: [] },
    ]);

    await mount({ currentSectorId: 5, currentSectorName: 'Rylan Prime', liveShipCount: 3 });

    const currentSection = container.querySelector('.tactical-current-section')!;
    expect(currentSection.textContent).toContain('RYLAN PRIME');

    const currentChip = currentSection.querySelector('.tactical-band-chip')!;
    expect(currentChip.textContent).toContain('HOSTILE');
    expect(currentChip.textContent).toContain('55');

    const live = currentSection.querySelector('.tactical-live-readout')!;
    expect(live.textContent).toContain('3 SHIPS DETECTED');
    expect(live.className).toContain('tactical-live-active');

    // Also present (marked CURRENT) in the full list below.
    const listId = Array.from(container.querySelectorAll('.tactical-sector-id')).find((el) =>
      el.textContent?.includes('SECTOR 5')
    );
    expect(listId?.textContent).toContain('(CURRENT)');
  });

  it('shows NO CONTACTS when no other ships are present', async () => {
    mockGetThreat.mockResolvedValue([{ sector_id: 5, score: 0, band: 'CLEAR', contributors: [] }]);

    await mount({ currentSectorId: 5, liveShipCount: 0 });

    const live = container.querySelector('.tactical-current-section .tactical-live-readout')!;
    expect(live.textContent).toBe('NO CONTACTS');
    expect(live.className).not.toContain('tactical-live-active');
  });

  // -------------------------------------------------------------------
  // Accessibility (Pixel a11y gate REVISE, WO-UI2-TACTICAL-MONITOR)
  // -------------------------------------------------------------------

  it('marks the CURRENT SECTOR label as a level-3 heading, nested under the TACTICAL level-2 header', async () => {
    mockGetThreat.mockResolvedValue([]);

    await mount({ currentSectorId: 5, currentSectorName: 'Rylan Prime' });

    const header = container.querySelector('.screen-hud-header span')!;
    expect(header.getAttribute('role')).toBe('heading');
    expect(header.getAttribute('aria-level')).toBe('2');

    const label = container.querySelector('.tactical-current-label')!;
    expect(label.getAttribute('role')).toBe('heading');
    expect(label.getAttribute('aria-level')).toBe('3');
    expect(label.textContent).toContain('CURRENT SECTOR');
  });
});
