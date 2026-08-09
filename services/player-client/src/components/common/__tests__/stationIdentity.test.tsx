// @vitest-environment jsdom
/**
 * stationIdentity — lookup helpers + StationClassBadge null-guard.
 * Pins the silent degrade contract for unknown/missing station classes.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  getStationClassInfo,
  getTraderPersonality,
  StationClassBadge,
} from '../stationIdentity';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('getStationClassInfo', () => {
  it('returns null for missing/empty/unknown values', () => {
    expect(getStationClassInfo(undefined)).toBeNull();
    expect(getStationClassInfo(null)).toBeNull();
    expect(getStationClassInfo('')).toBeNull();
    expect(getStationClassInfo('CLASS_99')).toBeNull();
    expect(getStationClassInfo('not-a-class')).toBeNull();
  });

  it('accepts numeric class, CLASS_N, and loose class-N strings', () => {
    expect(getStationClassInfo(1)?.name).toBe('Mining Operation');
    expect(getStationClassInfo('CLASS_2')?.name).toBe('Agricultural Center');
    expect(getStationClassInfo('class-3')?.blurb).toContain('buys equipment');
    expect(getStationClassInfo('4')?.group).toBe('trading');
  });
});

describe('getTraderPersonality', () => {
  it('normalizes keys and returns null for unknown personalities', () => {
    expect(getTraderPersonality(null)).toBeNull();
    expect(getTraderPersonality('frontier')).toEqual({
      key: 'FRONTIER',
      label: 'Rugged',
    });
    expect(getTraderPersonality('black-market')?.label).toBe('Discreet');
    expect(getTraderPersonality('UNKNOWN')).toBeNull();
  });
});

describe('StationClassBadge', () => {
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
  });

  it('renders nothing for an unrecognized class', async () => {
    await act(async () => {
      root.render(<StationClassBadge station_class="nope" />);
    });
    expect(container.querySelector('.station-class-badge')).toBeNull();
    expect(container.innerHTML).toBe('');
  });

  it('renders the canonical name chip for a known class', async () => {
    await act(async () => {
      root.render(<StationClassBadge station_class="CLASS_1" />);
    });
    const badge = container.querySelector('.station-class-badge');
    expect(badge).toBeTruthy();
    expect(badge?.textContent).toContain('Mining Operation');
    expect(container.querySelector('.station-class-mark')).toBeTruthy();
  });
});
