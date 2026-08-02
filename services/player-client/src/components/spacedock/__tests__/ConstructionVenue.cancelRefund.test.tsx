// @vitest-environment jsdom
/**
 * ConstructionVenue — cancel-confirm dialog reads the server-computed
 * `estimated_refund` advisory (WO-API-PHASE1 Lane B / B7) instead of
 * hand-rolling its own 0.7/0.5 fraction client-side. Thin-client: the field
 * comes straight off the reservation status payload (`/reservations/mine`),
 * same object already fetched for the card list.
 *
 * Also covers the graceful-degradation path (#139 lesson): an older/partial
 * payload missing `estimated_refund` must render "—" instead of crashing or
 * blanking the dialog.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { formatCredits } from '../../../utils/formatters';

vi.mock('../../../services/api', () => ({
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentShip: { cargo: { contents: { ore: 0, equipment: 0, organics: 0 } } },
    refreshPlayerState: vi.fn(),
    loadShips: vi.fn(),
  }),
}));

import ConstructionVenue from '../ConstructionVenue';

const BASE_RESERVATION = {
  id: 'res-1',
  state: 'frame_assembly',
  ship_type: 'scout',
  ship_name: 'My Scout',
  credits_paid: 6000,
  milestones: {
    deposit: { amount: 1000, paid: true },
    keel_laid: { amount: 1500, paid: true },
    hull_complete: { amount: 2500, paid: true },
    final: { amount: 4000, paid: false },
  },
  resources_required: { ore: 100, equipment: 50, organics: 20 },
  resources_delivered: {},
};

function mockFetch(reservation: Record<string, unknown>) {
  return vi.fn((url: string) => {
    if (url.includes('/construction/quotes')) {
      return Promise.resolve({ ok: true, json: async () => ({ quotes: [] }) });
    }
    if (url.includes('/construction/reservations/mine')) {
      return Promise.resolve({ ok: true, json: async () => ({ reservations: [reservation] }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

const VENUE_PROPS = {
  stationId: 'station-1',
  stationName: 'Test Dock',
  tier: 'A' as const,
  credits: 100000,
  onCreditsDelta: vi.fn(),
  onCreditsSet: vi.fn(),
  onBack: vi.fn(),
};

async function openCancelDialog(container: HTMLElement) {
  const buildsTab = Array.from(container.querySelectorAll('button'))
    .find((b) => b.textContent?.includes('My Builds'));
  expect(buildsTab).toBeTruthy();
  await act(async () => { buildsTab!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

  const cancelBtn = Array.from(container.querySelectorAll('button'))
    .find((b) => b.textContent?.trim() === 'Cancel');
  expect(cancelBtn).toBeTruthy();
  await act(async () => { cancelBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

describe('ConstructionVenue — cancel dialog estimated_refund', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('shows the server-computed estimated_refund (post-hull, 70%) in the cancel-confirm panel', async () => {
    vi.stubGlobal('fetch', mockFetch({ ...BASE_RESERVATION, estimated_refund: 4200 }));

    await act(async () => { root.render(<ConstructionVenue {...VENUE_PROPS} />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await openCancelDialog(container);

    const text = container.textContent || '';
    expect(text).toContain('Estimated refund (70%)');
    expect(text).toContain(formatCredits(4200));
    // The client must not be hand-computing its own 6000*0.7=4200 in a way
    // that would coincidentally pass this assertion for the wrong reason --
    // cross-check against a value that only the server-supplied field could
    // produce (a deliberately non-70%-of-credits_paid number).
  });

  it('reflects a server estimated_refund that is NOT a naive fraction of credits_paid (proves it is read, not recomputed)', async () => {
    // credits_paid=6000 but estimated_refund=1 -- if the component were still
    // hand-rolling 0.5/0.7 * credits_paid client-side, this would render
    // 4200 or 3000, never 1.
    vi.stubGlobal('fetch', mockFetch({ ...BASE_RESERVATION, estimated_refund: 1 }));

    await act(async () => { root.render(<ConstructionVenue {...VENUE_PROPS} />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await openCancelDialog(container);

    const text = container.textContent || '';
    expect(text).toContain(formatCredits(1));
    expect(text).not.toContain(formatCredits(4200));
    expect(text).not.toContain(formatCredits(3000));
  });

  it('gracefully degrades to "—" when estimated_refund is absent from the payload (no crash, no blank dialog)', async () => {
    const { estimated_refund: _omit, ...reservationWithoutField } = { ...BASE_RESERVATION, estimated_refund: 4200 };
    vi.stubGlobal('fetch', mockFetch(reservationWithoutField));

    await act(async () => { root.render(<ConstructionVenue {...VENUE_PROPS} />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await openCancelDialog(container);

    // Dialog still renders (no white-screen) with the credits-paid row intact.
    const text = container.textContent || '';
    expect(text).toContain('Cancel Build');
    expect(text).toContain('Credits paid so far');
    expect(text).toContain(formatCredits(6000));
    expect(text).toContain('Estimated refund (70%)');
    expect(text).toContain('—');
  });
});
