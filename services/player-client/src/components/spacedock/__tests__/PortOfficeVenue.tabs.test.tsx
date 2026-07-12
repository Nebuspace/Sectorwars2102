// @vitest-environment jsdom
/**
 * PortOfficeVenue — DeckPageTabs migration (WO-UI2-CANON-A-PORTOFFICE).
 *
 * Confirms the hand-rolled `.po-tab` tablist's behavior survived the move
 * onto the shared DeckPageTabs rail: the Owner Console page is filtered
 * out (not disabled) for a non-owner, appears for the owner, tab-switching
 * still works, the tabpanel aria contract (`po-panel-{id}` /
 * `po-tab-{id}`) is wired, and the stale-owner-panel guard (activeTab
 * left on 'owner' after ownership is lost mid-session) still resolves to
 * the registry view with no owner content rendered.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const OWNER_ID = 'player-1';
const OTHER_ID = 'player-2';

const ownedListing = {
  owner_id: OWNER_ID,
  owner_name: 'You',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 5000
};

const soldListing = {
  owner_id: OTHER_ID,
  owner_name: 'Rival Co',
  status: 'owned',
  is_listed: false,
  tax_rate: 0.1,
  treasury_balance: 0
};

let listingResponses: unknown[] = [];
const getListing = vi.fn(() => Promise.resolve(listingResponses.shift() ?? soldListing));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: OWNER_ID },
    getListing,
    listStation: vi.fn(),
    placeOffer: vi.fn(),
    getMyStations: vi.fn(() => Promise.resolve([])),
    setStationTax: vi.fn(),
    withdrawTreasury: vi.fn(),
    getTakeoverStatus: vi.fn(() => Promise.resolve({})),
    launchTakeover: vi.fn(),
    counterTakeover: vi.fn()
  })
}));

import PortOfficeVenue from '../PortOfficeVenue';

const VENUE_PROPS = {
  stationId: 'station-1',
  stationName: 'Test Port',
  credits: 100000,
  onCreditsSet: vi.fn(),
  onBack: vi.fn()
};

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('PortOfficeVenue — DeckPageTabs migration', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    listingResponses = [];
    getListing.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.useRealTimers();
  });

  it('non-owner sees 2 tabs (Owner Console filtered, not disabled)', async () => {
    listingResponses = [soldListing];
    await act(async () => { root.render(<PortOfficeVenue {...VENUE_PROPS} />); });
    await flush();

    const tabs = container.querySelectorAll('[role="tab"]');
    expect(tabs.length).toBe(2);
    const labels = Array.from(tabs).map(t => t.textContent);
    expect(labels.some(l => l?.includes('Registry'))).toBe(true);
    expect(labels.some(l => l?.includes('War Room'))).toBe(true);
    expect(labels.some(l => l?.includes('Owner Console'))).toBe(false);
  });

  it('owner sees 3 tabs and switching to Owner Console wires the tabpanel aria contract', async () => {
    listingResponses = [ownedListing];
    await act(async () => { root.render(<PortOfficeVenue {...VENUE_PROPS} />); });
    await flush();

    const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
    expect(tabs.length).toBe(3);
    const ownerTab = tabs.find(t => t.textContent?.includes('Owner Console')) as HTMLButtonElement;
    expect(ownerTab).toBeTruthy();
    expect(ownerTab.id).toBe('po-tab-owner');

    await act(async () => { ownerTab.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await flush();

    const panel = container.querySelector('[role="tabpanel"]');
    expect(panel).toBeTruthy();
    expect(panel!.id).toBe('po-panel-owner');
    expect(panel!.getAttribute('aria-labelledby')).toBe('po-tab-owner');
    expect(container.querySelector('.po-owner-console')).toBeTruthy();
    expect(container.querySelector('.po-registry')).toBeFalsy();
  });

  it('falls back off a stale owner panel once ownership is lost on refetch (no owner content leaks)', async () => {
    vi.useFakeTimers();
    listingResponses = [ownedListing, soldListing];
    await act(async () => { root.render(<PortOfficeVenue {...VENUE_PROPS} />); });
    await flush();

    const ownerTab = Array.from(container.querySelectorAll('[role="tab"]'))
      .find(t => t.textContent?.includes('Owner Console')) as HTMLButtonElement;
    await act(async () => { ownerTab.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await flush();
    expect(container.querySelector('.po-owner-console')).toBeTruthy();

    // The 30s poll refetches the listing — this time the station has sold.
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    await flush();

    expect(container.querySelector('.po-owner-console')).toBeFalsy();
    expect(container.querySelectorAll('[role="tab"]').length).toBe(2);
    const panel = container.querySelector('[role="tabpanel"]');
    expect(panel!.id).toBe('po-panel-registry');
  });
});
