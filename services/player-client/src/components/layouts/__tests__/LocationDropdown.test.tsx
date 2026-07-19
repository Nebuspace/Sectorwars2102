// @vitest-environment jsdom
/**
 * LocationDropdown — icon-only trigger + clean region name (WO-HUD-SHIPTYPE,
 * sector-move + dropdown clean-name amendment, Max ruled 2026-07-19).
 *
 * With region+sector both relocated to the windshield's `.locrow` glass
 * chip (GameDashboard.tsx, proven in GameDashboard.locrowGlassRetirement.
 * test.tsx), the status bar's collapsed [◉ location ▾] trigger dropped ALL
 * text -- icon + caret only, `aria-label="Location"` supplying the
 * accessible name the now-absent text label used to give it. The opened
 * panel is otherwise unchanged EXCEPT the region-name line, which now
 * strips the dev-seeded galaxy-id prefix ("Stage2 Genesis R4 — Terran
 * Space" -> "Terran Space") via `cleanRegionName()` rather than showing the
 * raw string.
 *
 * Mirrors StatusBar.lowTurns.test.tsx's mutable-mock-per-test seam (jsdom +
 * react-dom/client createRoot + act(), no RTL in this project). `region_id`
 * is left undefined on every fixture so CitizenshipBadge's own effect
 * no-ops (`if (!regionId) return`) without needing to mock services/api --
 * the same precedent StatusBar.smoke.test.tsx's mockSector already sets.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

let mockCurrentSector: Record<string, unknown> | null = null;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: mockCurrentSector,
    playerState: { is_landed: false, is_docked: false },
    planetsInSector: [],
    stationsInSector: [],
  }),
}));

import LocationDropdown from '../LocationDropdown';

const baseSector = {
  sector_id: 45,
  sector_number: 45,
  type: 'nebula',
};

describe('LocationDropdown — icon-only trigger + clean region name', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    mockCurrentSector = null;
  });

  const render = async () => {
    await act(async () => {
      root.render(<LocationDropdown />);
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('trigger is icon-only — no "Sector N" (or any) text label — with aria-label="Location" as its accessible name', async () => {
    mockCurrentSector = { ...baseSector, region_name: 'Terran Space' };
    await render();

    const trigger = container.querySelector('.sb-location-chip') as HTMLButtonElement;
    expect(trigger).not.toBeNull();
    expect(trigger.getAttribute('aria-label')).toBe('Location');
    // The icon is the only visible content -- no sb-location-text span, and
    // no stray "Sector"/"NEBULA" text anywhere in the trigger's own text.
    expect(trigger.querySelector('.sb-location-text')).toBeNull();
    expect(trigger.textContent?.trim()).toBe('◉');
    expect(trigger.textContent).not.toContain('Sector');
  });

  it('opened panel: cleans a dev-prefixed region_name ("Stage2 Genesis R4 — Terran Space" -> "Terran Space")', async () => {
    mockCurrentSector = { ...baseSector, region_name: 'Stage2 Genesis R4 — Terran Space' };
    await render();

    await click(container.querySelector('.sb-location-chip')!);

    const regionLine = container.querySelector('.sb-location-header-region');
    expect(regionLine).not.toBeNull();
    expect(regionLine?.textContent).toBe('Terran Space');
    expect(regionLine?.textContent).not.toContain('Stage2');
    expect(regionLine?.textContent).not.toContain('Genesis R4');

    // Sector identity is still one click away in the panel (only the
    // collapsed trigger dropped it, per the sector-move ruling).
    expect(container.querySelector('.sb-location-header-sector')?.textContent).toBe('Sector 45');
  });

  it('opened panel: a real custom region name with no "— " separator passes through unchanged (never a guess)', async () => {
    mockCurrentSector = { ...baseSector, region_name: 'The Frontier' };
    await render();

    await click(container.querySelector('.sb-location-chip')!);

    expect(container.querySelector('.sb-location-header-region')?.textContent).toBe('The Frontier');
  });

  it('opened panel: strips only up to the LAST "— " separator when a region name has more than one', async () => {
    mockCurrentSector = { ...baseSector, region_name: 'Stage2 — Sub-Cluster — Terran Space' };
    await render();

    await click(container.querySelector('.sb-location-chip')!);

    expect(container.querySelector('.sb-location-header-region')?.textContent).toBe('Terran Space');
  });

  it('opened panel: renders nothing for the region line when region_name is absent (no dangling label)', async () => {
    mockCurrentSector = { ...baseSector, region_name: null };
    await render();

    await click(container.querySelector('.sb-location-chip')!);

    expect(container.querySelector('.sb-location-header-region')).toBeNull();
  });
});
