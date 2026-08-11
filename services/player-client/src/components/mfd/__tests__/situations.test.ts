/**
 * situations — deriveInitialPage, the situation-derived INITIAL page
 * default consulted once per screen at hydration time (Design B graft).
 * Pure function; no rendering harness needed.
 */
import { describe, expect, it } from 'vitest';
import { deriveInitialPage } from '../situations';
import type { MFDSnapshot } from '../mfdTypes';

const snapshotWith = (playerState: unknown): MFDSnapshot => ({
  currentShip: null,
  playerState,
  currentSector: null,
  isConnected: true,
});

describe('deriveInitialPage — sidebar-a', () => {
  it('defaults to cargo when the player is docked', () => {
    expect(deriveInitialPage('sidebar-a', snapshotWith({ is_docked: true }))).toBe('cargo');
  });

  it('defaults to cargo when the player is landed', () => {
    expect(deriveInitialPage('sidebar-a', snapshotWith({ is_landed: true }))).toBe('cargo');
  });

  it('defaults to vessel-status when neither docked nor landed', () => {
    expect(
      deriveInitialPage('sidebar-a', snapshotWith({ is_docked: false, is_landed: false }))
    ).toBe('vessel-status');
  });

  it('defaults to vessel-status when playerState is null', () => {
    expect(deriveInitialPage('sidebar-a', snapshotWith(null))).toBe('vessel-status');
  });

  it('defaults to vessel-status when playerState is not an object', () => {
    expect(deriveInitialPage('sidebar-a', snapshotWith('not-an-object'))).toBe('vessel-status');
    expect(deriveInitialPage('sidebar-a', snapshotWith(undefined))).toBe('vessel-status');
  });

  it('defaults to vessel-status when is_docked/is_landed are truthy but not strictly true', () => {
    expect(deriveInitialPage('sidebar-a', snapshotWith({ is_docked: 1 }))).toBe('vessel-status');
    expect(deriveInitialPage('sidebar-a', snapshotWith({ is_landed: 'yes' }))).toBe('vessel-status');
  });
});

describe('deriveInitialPage — sidebar-a-folded', () => {
  it('shares the same docked/landed-aware default as sidebar-a', () => {
    expect(deriveInitialPage('sidebar-a-folded', snapshotWith({ is_docked: true }))).toBe('cargo');
    expect(deriveInitialPage('sidebar-a-folded', snapshotWith({ is_landed: true }))).toBe('cargo');
    expect(deriveInitialPage('sidebar-a-folded', snapshotWith({}))).toBe('vessel-status');
  });
});

describe('deriveInitialPage — sidebar-b', () => {
  it('always defaults to nav-position, regardless of playerState', () => {
    expect(deriveInitialPage('sidebar-b', snapshotWith({ is_docked: true }))).toBe('nav-position');
    expect(deriveInitialPage('sidebar-b', snapshotWith(null))).toBe('nav-position');
    expect(deriveInitialPage('sidebar-b', snapshotWith({}))).toBe('nav-position');
  });
});
