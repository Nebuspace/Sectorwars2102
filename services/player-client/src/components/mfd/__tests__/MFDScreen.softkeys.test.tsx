// @vitest-environment jsdom
/**
 * MFDScreen's softkey-item builder — pure-function proof that the 5-slot
 * cap and disabled-in-place rendering (mfd/MFDSoftkeyRail.tsx's behavior
 * before WO-UI0-SHELL-TRANSPLANT folded it into the shared
 * common/SoftkeyRail.tsx primitive) survived the consolidation. Renders
 * nothing — `buildSoftkeyItems` is a pure MFDPageDef[] -> SoftkeyRailItem[]
 * mapper, tested directly rather than through the full context-heavy
 * MFDScreen component (no MFDScreen-level test file existed before this
 * WO either).
 *
 * WO-UI0-SHELL-TRANSPLANT Leaf L2 added `padSoftkeyItems` (the artifact's
 * fixed-5-slot middot blanks) plus a live-mount block that renders the
 * REAL common/SoftkeyRail.tsx (imported, not modified) with MFDScreen's
 * actual item shapes -- proving the `.skey`/`.skrow` classes, the 5-slot
 * blank fill, and the a11y contract (blanks skipped by arrow-nav, never
 * focusable) end to end without needing MFDScreen's own context providers.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi } from 'vitest';
import { MAX_SOFTKEYS, buildSoftkeyItems, padSoftkeyItems, BLANK_SOFTKEY_LABEL } from '../MFDScreen';
import SoftkeyRail from '../../common/SoftkeyRail';
import type { MFDPageDef, MFDPageId, MFDSnapshot } from '../mfdTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const emptySnapshot: MFDSnapshot = {
  currentShip: null,
  playerState: null,
  currentSector: null,
  isConnected: true,
};

const dummyComponent = React.lazy(() => Promise.resolve({ default: () => null }));

// The frozen MFDPageId union (mfdTypes.ts) doesn't include arbitrary test
// ids like 'a'/'stat' -- buildSoftkeyItems only ever compares this value
// for equality against a def.id built the same way below, so a cast is
// safe and keeps the fixtures short-lived/readable.
const pid = (id: string): MFDPageId => id as MFDPageId;

const page = (id: string, overrides: Partial<MFDPageDef> = {}): MFDPageDef => ({
  id: id as MFDPageDef['id'],
  title: id.toUpperCase(),
  softLabel: id.slice(0, 4).toUpperCase(),
  accent: '#00d9ff',
  status: 'shipped',
  Component: dummyComponent,
  ...overrides,
});

describe('MFDScreen — buildSoftkeyItems', () => {
  it('caps at MAX_SOFTKEYS (5) — a 6th visible page never becomes a key', () => {
    const pages = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => page(id));
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('a'), () => false, () => {});

    expect(MAX_SOFTKEYS).toBe(5);
    expect(items.length).toBe(5);
    expect(items.map((i) => i.key)).toEqual(['a', 'b', 'c', 'd', 'e']);
  });

  it('an unavailable-but-visible page renders disabled IN PLACE — never filtered out', () => {
    const pages = [
      page('stat'),
      page('cargo', { available: () => false }),
      page('qtm'),
    ];
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('stat'), () => false, () => {});

    expect(items.length).toBe(3);
    expect(items[0].disabled).toBe(false);
    expect(items[1].key).toBe('cargo');
    expect(items[1].disabled).toBe(true);
    expect(items[2].disabled).toBe(false);
  });

  it('a throwing available predicate is treated as disabled (fail-closed, mfdRegistry contract)', () => {
    const pages = [
      page('stat', {
        available: () => {
          throw new Error('boom');
        },
      }),
    ];
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('stat'), () => false, () => {});
    expect(items[0].disabled).toBe(true);
  });

  it('the active page resolves selected:true; others false', () => {
    const pages = [page('stat'), page('cargo')];
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('cargo'), () => false, () => {});
    expect(items[0].selected).toBe(false);
    expect(items[1].selected).toBe(true);
  });

  it('a non-active alerted page gets an " — alert" aria-label suffix (visual alert moved to MFDScreen\'s itemClassName -- `.amberlit`, not a badge in the label)', () => {
    const pages = [page('stat'), page('cargo', { title: 'CARGO', softLabel: 'CRGO' })];
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('stat'), (id) => id === 'cargo', () => {});

    expect(items[1].ariaLabel).toBe('CARGO — alert');
    // WO-UI0-SHELL-TRANSPLANT Leaf L2: the old `.mfd-key-badge` pulsing
    // dot embedded in `label` is retired -- label is now plain text, the
    // artifact's `.skey`/`.skey.amberlit` frame has no badge concept.
    expect(items[1].label).toBe('CRGO');
  });

  it('the active page never carries the alert badge even if alerted() is true (self-badging is suppressed)', () => {
    const pages = [page('stat')];
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('stat'), () => true, () => {});
    // isActive && hasAlert -> alerted is forced false by `&& !isActive`.
    expect(items[0].ariaLabel).toBe('STAT');
  });

  it('onSelect wires back to the caller with the page id', () => {
    const pages = [page('stat'), page('cargo')];
    let selected: string | null = null;
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('stat'), () => false, (id) => {
      selected = id;
    });
    items[1].onSelect();
    expect(selected).toBe('cargo');
  });
});

describe('MFDScreen — padSoftkeyItems (artifact fixed-5-slot middot blanks)', () => {
  it('MFD-B\'s 2 real keys (POS/COMM) pad to exactly 3 blanks, matching the artifact\'s mfdBkeys 1:1', () => {
    const pages = [page('nav-position', { softLabel: 'POS' }), page('comms-crew', { softLabel: 'COMM' })];
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('nav-position'), () => false, () => {});
    const padded = padSoftkeyItems(items);

    expect(padded.length).toBe(MAX_SOFTKEYS);
    expect(padded.slice(0, 2)).toEqual(items);
    for (const blank of padded.slice(2)) {
      expect(blank.label).toBe(BLANK_SOFTKEY_LABEL);
      expect(blank.disabled).toBe(true);
      expect(blank.selected).toBe(false);
    }
    // Unique React keys -- a naive fill could collide.
    expect(new Set(padded.map((i) => i.key)).size).toBe(MAX_SOFTKEYS);
  });

  it('already-5 real keys (MFD-A on a Warp Jumper) pad to ZERO blanks -- never exceeds MAX_SOFTKEYS', () => {
    const pages = ['a', 'b', 'c', 'd', 'e'].map((id) => page(id));
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('a'), () => false, () => {});
    const padded = padSoftkeyItems(items);
    expect(padded.length).toBe(MAX_SOFTKEYS);
    expect(padded).toEqual(items);
  });

  it('a blank\'s onSelect is a safe no-op (never wired to any page)', () => {
    const padded = padSoftkeyItems([]);
    expect(() => padded[0].onSelect()).not.toThrow();
  });
});

describe('MFDScreen — SoftkeyRail live-mount (real common/SoftkeyRail.tsx, not modified)', () => {
  // Reproduces MFDScreen's exact itemClassName policy so this proves the
  // REAL composition the component renders, not just each piece alone.
  const mfdItemClassName = (item: { key: string; selected: boolean }, alertedKeys: Set<string>): string => {
    let cls = 'skey';
    if (item.selected) cls += ' lit';
    if (alertedKeys.has(item.key)) cls += ' amberlit';
    return cls;
  };

  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  const flush = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  const mountRail = async (items: ReturnType<typeof padSoftkeyItems>, alertedKeys: Set<string> = new Set()) => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <SoftkeyRail
          items={items}
          ariaLabel="MFD-B pages"
          railClassName="skrow"
          itemClassName={(item) => mfdItemClassName(item, alertedKeys)}
          accentVar="--mfd-key-accent"
          activateOnArrow={false}
          homeEnd={false}
        />,
      );
    });
    await flush();
  };

  const cleanup = async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  };

  it('MFD-B (POS active, COMM idle) renders .skrow > 5 .skey buttons -- 2 real (POS lit) + 3 disabled middot blanks', async () => {
    const pages = [page('nav-position', { softLabel: 'POS' }), page('comms-crew', { softLabel: 'COMM' })];
    const items = padSoftkeyItems(
      buildSoftkeyItems(pages, emptySnapshot, pid('nav-position'), () => false, () => {}),
    );
    await mountRail(items);

    expect(container.querySelector('.skrow')).not.toBeNull();
    const keys = Array.from(container.querySelectorAll('.skey')) as HTMLButtonElement[];
    expect(keys.length).toBe(5);
    expect(keys[0].textContent).toBe('POS');
    expect(keys[0].className).toBe('skey lit');
    expect(keys[1].textContent).toBe('COMM');
    expect(keys[1].className).toBe('skey');
    for (const blank of keys.slice(2)) {
      expect(blank.textContent).toBe(BLANK_SOFTKEY_LABEL);
      expect(blank.disabled).toBe(true);
      expect(blank.getAttribute('aria-disabled')).toBe('true');
      expect(blank.className).toBe('skey');
    }
    await cleanup();
  });

  it('an alerted idle key gets .amberlit (whole-key highlight, replaces the old badge dot)', async () => {
    const pages = [page('nav-position', { softLabel: 'POS' }), page('comms-crew', { softLabel: 'COMM' })];
    const items = padSoftkeyItems(
      buildSoftkeyItems(pages, emptySnapshot, pid('nav-position'), (id) => id === 'comms-crew', () => {}),
    );
    await mountRail(items, new Set(['comms-crew']));

    const keys = Array.from(container.querySelectorAll('.skey')) as HTMLButtonElement[];
    expect(keys[1].className).toBe('skey amberlit');
    await cleanup();
  });

  it('a11y: blank slots are skipped by ArrowRight nav and never take the roving tabindex', async () => {
    const pages = [page('nav-position', { softLabel: 'POS' }), page('comms-crew', { softLabel: 'COMM' })];
    const items = padSoftkeyItems(
      buildSoftkeyItems(pages, emptySnapshot, pid('nav-position'), () => false, () => {}),
    );
    await mountRail(items);

    const keys = () => Array.from(container.querySelectorAll('.skey')) as HTMLButtonElement[];
    // Roving tabindex: exactly one stop, on the selected real key -- the
    // three trailing blanks are never reachable via Tab at all.
    expect(keys().filter((b) => b.tabIndex === 0).length).toBe(1);
    expect(keys()[0].tabIndex).toBe(0);
    expect(keys().slice(2).every((b) => b.tabIndex === -1)).toBe(true);

    // ArrowRight from POS (index 0): COMM (1) is the only other enabled
    // key -- disabled blanks (2,3,4) are skipped entirely, wrapping focus
    // straight to COMM, never landing on a blank.
    await act(async () => {
      keys()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    });
    await flush();
    expect(document.activeElement).toBe(keys()[1]);

    await cleanup();
  });

  it('a blank button is natively un-clickable/un-selectable -- no onSelect ever fires from it', async () => {
    const onSelect = vi.fn();
    const items = padSoftkeyItems([
      { key: 'stat', label: 'STAT', selected: true, onSelect, disabled: false },
    ]);
    await mountRail(items);
    const keys = Array.from(container.querySelectorAll('.skey')) as HTMLButtonElement[];
    // jsdom still honors the native `disabled` attribute for .click().
    await act(async () => {
      keys[1].click();
    });
    expect(onSelect).not.toHaveBeenCalled();
    await cleanup();
  });
});
