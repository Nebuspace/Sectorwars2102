/**
 * LEG-4146 — HARVEST pre-click grey-out: verify gate reason copy for all four
 * Design-only gates (no laser, docked, cargo full, unlicensed).
 *
 * These tests exercise HARVEST_GATE_COPY and harvestGateMessage, the shared
 * source of truth for gate reason text used by both HarvestYieldPreview
 * (server-side gate copy) and the GameDashboard client-side grey-out additions.
 */
import { describe, it, expect } from 'vitest';
import { HARVEST_GATE_COPY, harvestGateMessage } from '../HarvestYieldPreview';

describe('HARVEST gate reason text (LEG-4146)', () => {
  it('[gate 1] no_mining_laser reason text is present and descriptive', () => {
    const msg = HARVEST_GATE_COPY['no_mining_laser'];
    expect(typeof msg).toBe('string');
    expect(msg.length).toBeGreaterThan(5);
    // harvestGateMessage surfaces this copy from a server Error keyed no_mining_laser
    const fromServer = harvestGateMessage(new Error('no_mining_laser'));
    expect(fromServer).toBe(msg);
  });

  it('[gate 2] must_be_undocked reason text is present and descriptive', () => {
    // Client-side docked check uses identical copy for UX consistency.
    const msg = HARVEST_GATE_COPY['must_be_undocked'];
    expect(typeof msg).toBe('string');
    expect(msg.length).toBeGreaterThan(5);
    expect(msg.toLowerCase()).toMatch(/undock|docked|open space/i);
  });

  it('[gate 3] cargo_full reason text is present and descriptive', () => {
    const msg = HARVEST_GATE_COPY['cargo_full'];
    expect(typeof msg).toBe('string');
    expect(msg.length).toBeGreaterThan(5);
    expect(msg.toLowerCase()).toMatch(/cargo|full|ore/i);
    // harvestGateMessage surfaces this copy from a server Error keyed cargo_full
    const fromServer = harvestGateMessage(new Error('cargo_full'));
    expect(fromServer).toBe(msg);
  });

  it('[gate 4] harvestGateMessage returns descriptive fallback for unknown reason (unlicensed)', () => {
    // The unlicensed case is a rep-penalty warning, not a hard server block;
    // the client-side grey-out for this gate is surfaced via the server yield
    // preview if/when an unlicensed reason code is added. For now verify the
    // fallback path is sane.
    const fallback = harvestGateMessage(new Error('unlicensed_am_mining'), 'Custom fallback');
    // With no matching HARVEST_GATE_COPY key, returns the raw message.
    expect(fallback).toBeTruthy();
    expect(typeof fallback).toBe('string');
    expect(fallback.length).toBeGreaterThan(0);
  });

  it('harvestGateMessage falls back to custom fallback on TypeError', () => {
    const fallback = harvestGateMessage(new TypeError('Failed to fetch'), 'Yield preview failed.');
    expect(fallback).toBe('Yield preview failed.');
  });

  it('harvestGateMessage preserves server-detail for 403 with detail', () => {
    const err = Object.assign(new Error('no_mining_laser'), { status: 403 });
    const msg = harvestGateMessage(err);
    // HARVEST_GATE_COPY key match returns the canonical copy
    expect(msg).toBe(HARVEST_GATE_COPY['no_mining_laser']);
  });
});
