// @vitest-environment jsdom
/**
 * windshieldTableauChrome — pure presentational render blocks extracted from
 * WindshieldTableau.tsx (hazard haze, scan-gated wrecks/formations, message
 * beacons, the player ship marker + warp cinematic). Every export is a
 * stateless function of its props -> JSX, so these tests render each one
 * directly (no context/provider scaffolding needed) via jsdom + createRoot +
 * act(), mirroring the MFD-page test seam minus the GameContext mock.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  HazardArcsLayer,
  ScanLayer,
  BeaconLayer,
  PlayerShipAndWarpLayer,
} from '../windshieldTableauChrome';
import type { StarAnchor, HazardArc, PctPoint } from '../windshieldTableauLayout';
import type { SectorWreck } from '../../../services/api';
import type { SpecialFormationSummary } from '../../../contexts/GameContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const star: StarAnchor = { xPct: 50, yPct: 50, sizeEm: 3 };
const arc = (overrides: Partial<HazardArc> = {}): HazardArc => ({
  rFrac: 0.5,
  startDeg: 0,
  sweepDeg: 90,
  ...overrides,
});

describe('windshieldTableauChrome', () => {
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

  const render = async (node: React.ReactElement) => {
    await act(async () => {
      root.render(node);
    });
  };

  describe('HazardArcsLayer', () => {
    it('renders nothing when there are no haze arcs and no debris ring', async () => {
      await render(
        <HazardArcsLayer star={star} hazeArcs={[]} debrisRingArc={null} nebula={null} debris={null} />
      );
      expect(container.querySelector('svg.hazard-arcs')).toBeNull();
    });

    it('renders one path per haze arc', async () => {
      await render(
        <HazardArcsLayer
          star={star}
          hazeArcs={[arc(), arc({ startDeg: 90 }), arc({ startDeg: 180 })]}
          debrisRingArc={null}
          nebula={{ hue: 260, density: 0.3 }}
          debris={null}
        />
      );
      const svg = container.querySelector('svg.hazard-arcs');
      expect(svg).not.toBeNull();
      expect(svg?.querySelectorAll('path').length).toBe(3);
    });

    it('renders the debris path only when both debrisRingArc and debris are present', async () => {
      await render(
        <HazardArcsLayer
          star={star}
          hazeArcs={[]}
          debrisRingArc={arc()}
          nebula={null}
          debris={{ inner_au: 0.2, outer_au: 0.4, hue: 40 }}
        />
      );
      const svg = container.querySelector('svg.hazard-arcs');
      expect(svg?.querySelectorAll('path').length).toBe(1);
      expect(svg?.querySelector('path')?.getAttribute('stroke')).toContain('hsla(40,');
    });

    it('omits the debris path when debrisRingArc is set but debris data is null', async () => {
      await render(
        <HazardArcsLayer star={star} hazeArcs={[]} debrisRingArc={arc()} nebula={null} debris={null} />
      );
      // hazeArcs.length===0 but debrisRingArc is truthy, so the svg itself renders...
      const svg = container.querySelector('svg.hazard-arcs');
      expect(svg).not.toBeNull();
      // ...but the debris path itself is gated on `debris` too, so none renders.
      expect(svg?.querySelectorAll('path').length).toBe(0);
    });

    it('falls back to the default hue/density when nebula is null', async () => {
      await render(
        <HazardArcsLayer star={star} hazeArcs={[arc()]} debrisRingArc={null} nebula={null} debris={null} />
      );
      const stroke = container.querySelector('svg.hazard-arcs path')?.getAttribute('stroke');
      expect(stroke).toContain('hsla(260,');
    });

    it('clamps the haze stroke opacity into [0.1, 0.4] at extreme densities', async () => {
      await render(
        <HazardArcsLayer
          star={star}
          hazeArcs={[arc()]}
          debrisRingArc={null}
          nebula={{ hue: 0, density: 999 }}
          debris={null}
        />
      );
      const stroke = container.querySelector('svg.hazard-arcs path')?.getAttribute('stroke');
      expect(stroke).toBe('hsla(0, 70%, 55%, 0.4)');
    });
  });

  describe('ScanLayer', () => {
    const wreck = (overrides: Partial<SectorWreck> = {}): SectorWreck => ({
      id: 'wreck-1',
      original_owner_id: null,
      original_owner_name: null,
      destroyed_ship_type: 'SCOUT',
      cause: 'combat',
      created_at: '2026-01-01T00:00:00Z',
      age_seconds: 60,
      cargo: {},
      would_flag_suspect: false,
      ...overrides,
    });

    const formation = (overrides: Partial<SpecialFormationSummary> = {}): SpecialFormationSummary => ({
      id: 'form-1',
      is_discovered: true,
      is_anchor: false,
      name: 'Silent Reef',
      type: 'reef',
      ...overrides,
    });

    it('renders nothing when scanActive is false, even with wrecks/formations present', async () => {
      const onOpenPopup = vi.fn();
      await render(
        <ScanLayer scanActive={false} wrecks={[wreck()]} formations={[formation()]} star={star} onOpenPopup={onOpenPopup} />
      );
      expect(container.querySelector('.obj')).toBeNull();
      expect(container.querySelector('.anom')).toBeNull();
    });

    it('renders a wreck button and calls onOpenPopup with wreck meta on click', async () => {
      const onOpenPopup = vi.fn();
      await render(
        <ScanLayer
          scanActive
          wrecks={[wreck({ id: 'wreck-9', destroyed_ship_type: 'FREIGHTER', cause: 'pirate', would_flag_suspect: true })]}
          formations={[]}
          star={star}
          onOpenPopup={onOpenPopup}
        />
      );
      const btn = container.querySelector('button.obj') as HTMLButtonElement;
      expect(btn.getAttribute('aria-label')).toBe('Wreckage — FREIGHTER');
      await act(async () => {
        btn.click();
      });
      expect(onOpenPopup).toHaveBeenCalledTimes(1);
      const [meta, name] = onOpenPopup.mock.calls[0];
      expect(meta).toMatchObject({ kind: 'wreck', wreckId: 'wreck-9', shipType: 'FREIGHTER', cause: 'pirate', suspect: true });
      expect(name).toBe('WRECKAGE');
    });

    it('renders a discovered formation with its uppercased name and discovered=true meta', async () => {
      const onOpenPopup = vi.fn();
      await render(
        <ScanLayer
          scanActive
          wrecks={[]}
          formations={[formation({ id: 'form-a', name: 'silent reef', is_discovered: true })]}
          star={star}
          onOpenPopup={onOpenPopup}
        />
      );
      const btn = container.querySelector('button.obj') as HTMLButtonElement;
      expect(btn.querySelector('.objtag')?.textContent).toBe('SILENT REEF');
      await act(async () => {
        btn.click();
      });
      expect(onOpenPopup.mock.calls[0][0]).toMatchObject({ kind: 'formation', formationId: 'form-a', discovered: true });
    });

    it('falls back a discovered-but-unnamed formation to DERELICT BEACON', async () => {
      const onOpenPopup = vi.fn();
      await render(
        <ScanLayer
          scanActive
          wrecks={[]}
          formations={[formation({ name: null, is_discovered: true })]}
          star={star}
          onOpenPopup={onOpenPopup}
        />
      );
      expect(container.querySelector('.objtag')?.textContent).toBe('DERELICT BEACON');
    });

    it('renders an undiscovered formation as an unresolved-signal glyph with no objtag', async () => {
      const onOpenPopup = vi.fn();
      await render(
        <ScanLayer
          scanActive
          wrecks={[]}
          formations={[formation({ id: 'form-b', is_discovered: false })]}
          star={star}
          onOpenPopup={onOpenPopup}
        />
      );
      const btn = container.querySelector('button.anom') as HTMLButtonElement;
      expect(btn).not.toBeNull();
      expect(btn.getAttribute('aria-label')).toBe('Unresolved signal');
      expect(btn.querySelector('.objtag')).toBeNull();
      await act(async () => {
        btn.click();
      });
      const [meta, name] = onOpenPopup.mock.calls[0];
      expect(meta).toMatchObject({ kind: 'formation', formationId: 'form-b', name: null, discovered: false });
      expect(name).toBe('UNIDENTIFIED ANOMALY');
    });
  });

  describe('BeaconLayer', () => {
    it('renders nothing when there are no beacons', async () => {
      await render(<BeaconLayer beacons={[]} star={star} onOpenPopup={vi.fn()} />);
      expect(container.querySelector('button.obj')).toBeNull();
    });

    it('renders a beacon button labelled with the deployer nickname and passes its id as objectId', async () => {
      const onOpenPopup = vi.fn();
      await render(
        <BeaconLayer
          beacons={[{ id: 'beacon-1', deployer_nickname: 'Nova', deployed_at: null, preview: 'hi', expiry: null }]}
          star={star}
          onOpenPopup={onOpenPopup}
        />
      );
      const btn = container.querySelector('button.obj') as HTMLButtonElement;
      expect(btn.getAttribute('aria-label')).toBe('Message beacon — Nova');
      await act(async () => {
        btn.click();
      });
      const [meta, name, , objectId] = onOpenPopup.mock.calls[0];
      expect(meta).toMatchObject({ kind: 'beacon', beaconId: 'beacon-1', deployerNickname: 'Nova' });
      expect(name).toBe('MESSAGE BEACON');
      expect(objectId).toBe('beacon-1');
    });

    it('renders one button per beacon', async () => {
      await render(
        <BeaconLayer
          beacons={[
            { id: 'b1', deployer_nickname: 'Nova', deployed_at: null, preview: '', expiry: null },
            { id: 'b2', deployer_nickname: 'Rho', deployed_at: null, preview: '', expiry: null },
          ]}
          star={star}
          onOpenPopup={vi.fn()}
        />
      );
      expect(container.querySelectorAll('button.obj').length).toBe(2);
    });
  });

  describe('PlayerShipAndWarpLayer', () => {
    const pos: PctPoint = { xPct: 40, yPct: 60 };
    const baseProps = {
      shipPos: pos,
      shipMkRef: { current: null },
      burning: false,
      travelPhase: 'idle' as const,
      warpPhase: 'idle' as const,
      heading: 90,
      warpBearing: 0,
      arrivalBearing: 0,
    };

    it('renders nothing when shipPos is null', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} shipPos={null} />);
      expect(container.querySelector('.shipmk')).toBeNull();
    });

    it('adds the burning class only when burning is true', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} burning />);
      expect(container.querySelector('.shipmk')?.className).toContain('burning');
    });

    it('omits the burning class when burning is false', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} burning={false} />);
      expect(container.querySelector('.shipmk')?.className).not.toContain('burning');
    });

    it('adds a travel-<phase> class whenever travelPhase is not idle', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} travelPhase="brake-turn" />);
      expect(container.querySelector('.shipmk')?.className).toContain('travel-brake-turn');
    });

    it('adds no travel-* class when travelPhase is idle', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} travelPhase="idle" />);
      expect(container.querySelector('.shipmk')?.className).not.toContain('travel-');
    });

    it.each(['turning', 'launch', 'arriving'] as const)(
      'adds a warp-%s class on the ship marker',
      async (phase) => {
        await render(<PlayerShipAndWarpLayer {...baseProps} warpPhase={phase} />);
        expect(container.querySelector('.shipmk')?.className).toContain(`warp-${phase}`);
      }
    );

    it.each(['turning', 'orienting', 'brake-turn', 'halt-turn', 'redirect-turn', 'final-orient'] as const)(
      'renders RCS jets for %s attitude changes',
      async (value) => {
        const isWarp = value === 'turning';
        await render(
          <PlayerShipAndWarpLayer
            {...baseProps}
            warpPhase={isWarp ? 'turning' : 'idle'}
            travelPhase={isWarp ? 'idle' : value}
          />
        );
        expect(container.querySelectorAll('.ssv-rcs').length).toBe(2);
      }
    );

    it('renders no RCS jets when neither warpPhase nor travelPhase implies an attitude change', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} warpPhase="idle" travelPhase="gliding" />);
      expect(container.querySelectorAll('.ssv-rcs').length).toBe(0);
    });

    it('renders no warp cinematic when warpPhase is idle', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} warpPhase="idle" />);
      expect(container.querySelector('.ssv-warp')).toBeNull();
    });

    it('renders no warp cinematic while only turning (pre-launch)', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} warpPhase="turning" />);
      expect(container.querySelector('.ssv-warp')).toBeNull();
    });

    it('renders the warp cinematic once charging/launch/arriving', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} warpPhase="charging" />);
      const cinematic = container.querySelector('.ssv-warp');
      expect(cinematic).not.toBeNull();
      expect(cinematic?.className).toContain('warp-charging');
      expect(cinematic?.querySelector('.ssv-warp-bubble')).not.toBeNull();
      expect(cinematic?.querySelector('.ssv-warp-streak')).not.toBeNull();
    });

    it('renders the warp flash only on launch/arriving, not charging', async () => {
      await render(<PlayerShipAndWarpLayer {...baseProps} warpPhase="charging" />);
      expect(container.querySelector('.ssv-warp-flash')).toBeNull();

      await render(<PlayerShipAndWarpLayer {...baseProps} warpPhase="launch" />);
      expect(container.querySelector('.ssv-warp-flash')).not.toBeNull();
    });
  });
});
