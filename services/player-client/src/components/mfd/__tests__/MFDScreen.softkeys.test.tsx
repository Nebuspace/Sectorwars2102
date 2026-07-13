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
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect } from 'vitest';
import { MAX_SOFTKEYS, buildSoftkeyItems } from '../MFDScreen';
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

  it('a non-active alerted page gets an " — alert" aria-label suffix + a badge in its label', async () => {
    const pages = [page('stat'), page('cargo', { title: 'CARGO' })];
    const items = buildSoftkeyItems(pages, emptySnapshot, pid('stat'), (id) => id === 'cargo', () => {});

    expect(items[1].ariaLabel).toBe('CARGO — alert');

    // label is a ReactNode fragment (softLabel text + a badge span) --
    // render it to confirm the badge element is actually present.
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<>{items[1].label}</>);
    });
    expect(container.querySelector('.mfd-key-badge')).not.toBeNull();
    expect(container.textContent).toContain('CARG');
    await act(async () => {
      root.unmount();
    });
    container.remove();
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
