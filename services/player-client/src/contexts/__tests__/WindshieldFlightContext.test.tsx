// @vitest-environment jsdom
/**
 * WindshieldFlightContext — the shared flight-state store unifying the
 * windshield tableau's local click→glide with the SOLAR SYSTEM rows'
 * APPROACH/HALT and the locrow's ALL STOP chip (WO-UI2-FLIGHT-FEEL).
 *
 * Proves the Provider/hook contract in isolation, no WindshieldTableau or
 * GameDashboard involved (that's WindshieldTableau.test.tsx's own updated
 * coverage + the new GameDashboard.flightFeelUnifiedStore.test.tsx
 * integration proof). Mirrors the repo's dominant jsdom + react-dom/client
 * createRoot + act() seam, no RTL.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let autopilotState: { status: string; abort: ReturnType<typeof vi.fn> };
vi.mock('../AutopilotContext', () => ({
  useAutopilot: () => autopilotState,
}));

// eslint-disable-next-line import/first
import { WindshieldFlightProvider, useWindshieldFlight } from '../WindshieldFlightContext';

let captured: ReturnType<typeof useWindshieldFlight> | null = null;
function Consumer() {
  captured = useWindshieldFlight();
  return null;
}

describe('WindshieldFlightContext', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    autopilotState = { status: 'idle', abort: vi.fn() };
    captured = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  const mount = async () => {
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <Consumer />
        </WindshieldFlightProvider>
      );
    });
  };

  it('isFlying/targetId start false/null — idle autopilot, no local glide reported yet', async () => {
    await mount();
    expect(captured?.isFlying).toBe(false);
    expect(captured?.targetId).toBeNull();
    expect(captured?.arrivedTargetId).toBeNull();
    expect(captured?.pendingApproach).toBeNull();
    expect(captured?.stopSignal).toBe(0);
  });

  it('natural glide settle promotes last target to arrivedTargetId', async () => {
    await mount();
    await act(async () => { captured!.reportFlightState(true, 'planet-1'); });
    expect(captured?.arrivedTargetId).toBeNull();
    await act(async () => { captured!.reportFlightState(false, null); });
    expect(captured?.isFlying).toBe(false);
    expect(captured?.arrivedTargetId).toBe('planet-1');
  });

  it('allStop before settle does NOT set arrivedTargetId', async () => {
    await mount();
    await act(async () => { captured!.reportFlightState(true, 'planet-1'); });
    await act(async () => { captured!.allStop(); });
    // Halt animation still reports flying=true with the same target — must
    // not clear skipArrival, or the eventual settle would unlock LAND/DOCK.
    await act(async () => { captured!.reportFlightState(true, 'planet-1'); });
    await act(async () => { captured!.reportFlightState(false, null); });
    expect(captured?.arrivedTargetId).toBeNull();
  });

  it('free travel / autopilot with no body target does not set arrivedTargetId', async () => {
    await mount();
    await act(async () => { captured!.reportFlightState(true, null); });
    await act(async () => { captured!.reportFlightState(false, null); });
    expect(captured?.arrivedTargetId).toBeNull();
  });

  it('approach() clears arrivedTargetId so a new glide can start', async () => {
    await mount();
    await act(async () => { captured!.reportFlightState(true, 'planet-1'); });
    await act(async () => { captured!.reportFlightState(false, null); });
    expect(captured?.arrivedTargetId).toBe('planet-1');
    await act(async () => { captured!.approach('station-1'); });
    expect(captured?.arrivedTargetId).toBeNull();
  });

  it('isFlying is true while the REAL inter-sector autopilot is engaged, independent of any local glide', async () => {
    autopilotState.status = 'engaged';
    await mount();
    expect(captured?.isFlying).toBe(true);
  });

  it('reportFlightState(true, id) — the tableau publishing its local glide — makes isFlying/targetId true/set, with autopilot idle', async () => {
    await mount();
    await act(async () => {
      captured!.reportFlightState(true, 'planet-1');
    });
    expect(captured?.isFlying).toBe(true);
    expect(captured?.targetId).toBe('planet-1');
  });

  it('isFlying is the OR of local glide and real autopilot — either alone is enough, both false is not flying', async () => {
    await mount();
    await act(async () => { captured!.reportFlightState(false, null); });
    expect(captured?.isFlying).toBe(false);

    // Local glide alone.
    await act(async () => { captured!.reportFlightState(true, 'station-1'); });
    expect(captured?.isFlying).toBe(true);

    // Local glide ends, but a real course is now underway (a re-render with
    // a fresh autopilot mock would be needed to flip autopilot.status in a
    // real app — this asserts the OTHER half of the OR using the tableau's
    // own report channel returning to false).
    await act(async () => { captured!.reportFlightState(false, null); });
    expect(captured?.isFlying).toBe(false);
  });

  it('approach(id) records a pendingApproach with a FRESH seq every call, even repeat clicks on the same id (so a keyed tableau effect always re-fires)', async () => {
    await mount();
    await act(async () => { captured!.approach('planet-1'); });
    const first = captured!.pendingApproach;
    expect(first).toEqual({ objectId: 'planet-1', seq: 1 });

    await act(async () => { captured!.approach('planet-1'); });
    const second = captured!.pendingApproach;
    expect(second).toEqual({ objectId: 'planet-1', seq: 2 });
    expect(second).not.toBe(first);

    await act(async () => { captured!.approach('station-9'); });
    expect(captured?.pendingApproach).toEqual({ objectId: 'station-9', seq: 3 });
  });

  it('allStop() aborts the real autopilot course AND bumps stopSignal (for the tableau to freeze its local glide)', async () => {
    await mount();
    expect(captured?.stopSignal).toBe(0);

    await act(async () => { captured!.allStop(); });

    expect(autopilotState.abort).toHaveBeenCalledWith('all stop');
    expect(captured?.stopSignal).toBe(1);

    await act(async () => { captured!.allStop(); });
    expect(captured?.stopSignal).toBe(2);
  });
});
