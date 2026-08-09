/**
 * mfdRegistry — page lookup, alert channels, available/hidden fail modes.
 */
import { describe, expect, it } from 'vitest';
import {
  MFD_PAGES,
  getPageDef,
  isPageAvailable,
  isPageHidden,
  pagesForChannel,
} from '../mfdRegistry';
import type { MFDPageDef, MFDSnapshot } from '../mfdTypes';

const snap = (ship: unknown = null): MFDSnapshot =>
  ({ currentShip: ship } as unknown as MFDSnapshot);

describe('mfdRegistry', () => {
  it('exposes the five ratified MFD pages with soft labels', () => {
    expect(Object.keys(MFD_PAGES).sort()).toEqual([
      'cargo',
      'comms-crew',
      'nav-position',
      'quantum-drive',
      'vessel-status',
    ]);
    expect(getPageDef('cargo').softLabel).toBe('CRGO');
    expect(getPageDef('vessel-status').status).toBe('shipped');
  });

  it('maps alert channels to pages', () => {
    expect(pagesForChannel('autopilot-pause')).toEqual(['nav-position']);
    expect(pagesForChannel('new-message')).toEqual(['comms-crew']);
  });

  it('hides quantum-drive unless the ship is a WARP_JUMPER', () => {
    const qtm = MFD_PAGES['quantum-drive'];
    expect(isPageHidden(qtm, snap({ type: 'SCOUT_SHIP' }))).toBe(true);
    expect(isPageHidden(qtm, snap({ type: 'WARP_JUMPER' }))).toBe(false);
    expect(isPageHidden(qtm, snap(null))).toBe(true);
  });

  it('treats a throwing available predicate as unavailable', () => {
    const def = {
      available: () => {
        throw new Error('boom');
      },
    } as unknown as MFDPageDef;
    expect(isPageAvailable(def, snap())).toBe(false);
  });

  it('fail-opens a throwing hidden predicate (page stays visible)', () => {
    const def = {
      hidden: () => {
        throw new Error('boom');
      },
    } as unknown as MFDPageDef;
    expect(isPageHidden(def, snap())).toBe(false);
  });

  it('defaults available=true and hidden=false when predicates are absent', () => {
    const bare = {} as MFDPageDef;
    expect(isPageAvailable(bare, snap())).toBe(true);
    expect(isPageHidden(bare, snap())).toBe(false);
  });
});
