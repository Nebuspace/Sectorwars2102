// @vitest-environment jsdom
/**
 * SectorNode3D — geometry/color/opacity selection, player-count + label +
 * special-formation indicator gating, and click/hover handling. Follows
 * the established galaxy/__tests__ r3f convention (PlayerMarker3D.test.tsx,
 * ConnectionPath3D.test.tsx): render through plain react-dom (r3f intrinsic
 * tags like <group>/<mesh>/<meshStandardMaterial> pass through as inert
 * unknown DOM elements under jsdom, never a real Canvas), with drei's
 * Box/Cylinder/Sphere/Text/Html mocked to capture their props directly —
 * this lets a test read the still-unrendered <meshStandardMaterial> child
 * element's own props (color/opacity/emissive) straight off the captured
 * React element tree, without needing WebGL or a real Object3D to inspect.
 * `useFrame` is mocked to a no-op (captured, never invoked): SectorNode3D's
 * useFrame body dereferences groupRef.current.scale / meshRef.current.
 * material as real THREE.Object3D/Material fields, which the refs are NOT
 * under this non-Canvas render (they're plain HTMLUnknownElements) — so
 * this file only reference the animation's declared TARGET values
 * (targetScale/targetOpacity, asserted via the initial material props),
 * never the lerp step itself.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Vector3, Color } from 'three';
import type { Sector, SpecialFormationSummary } from '../../../contexts/GameContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('@react-three/fiber', () => ({
  useFrame: vi.fn(),
}));

type Captured = { children?: any; args?: any[]; [key: string]: any };
const captured = {
  box: [] as Captured[],
  cylinder: [] as Captured[],
  sphere: [] as Captured[],
  text: [] as Captured[],
  html: [] as Captured[],
};

vi.mock('@react-three/drei', () => ({
  Box: (props: Captured) => {
    captured.box.push(props);
    return <div data-testid="geo-box" />;
  },
  Cylinder: (props: Captured) => {
    captured.cylinder.push(props);
    return <div data-testid="geo-cylinder" />;
  },
  Sphere: (props: Captured) => {
    captured.sphere.push(props);
    return <div data-testid="geo-sphere" />;
  },
  Text: (props: Captured) => {
    captured.text.push(props);
    return <div data-testid="text3d">{props.children}</div>;
  },
  Html: (props: Captured) => {
    captured.html.push(props);
    return <div data-testid="html3d">{props.children}</div>;
  },
}));

import SectorNode3D from '../SectorNode3D';

const baseSector = (overrides: Partial<Sector> = {}): Sector => ({
  id: 1,
  sector_id: 1,
  name: 'Alpha Reach',
  type: 'normal',
  hazard_level: 0,
  radiation_level: 0,
  resources: {},
  players_present: [],
  ...overrides,
});

const lod = (over: Partial<{ detail: 'high' | 'medium' | 'low'; showLabels: boolean; showEffects: boolean }> = {}) => ({
  detail: 'high' as const,
  showLabels: true,
  showEffects: true,
  ...over,
});

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  captured.box.length = 0;
  captured.cylinder.length = 0;
  captured.sphere.length = 0;
  captured.text.length = 0;
  captured.html.length = 0;
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

const renderNode = (props: Partial<React.ComponentProps<typeof SectorNode3D>> & { sector: Sector }) => {
  const onClick = props.onClick ?? vi.fn();
  act(() => {
    root.render(
      <SectorNode3D
        sector={props.sector}
        position={props.position ?? new Vector3(0, 0, 0)}
        isSelected={props.isSelected ?? false}
        isCurrent={props.isCurrent ?? false}
        onClick={onClick}
        lodLevel={props.lodLevel ?? lod()}
        playerCount={props.playerCount ?? 0}
        knowledge={props.knowledge}
        clickable={props.clickable}
      />
    );
  });
  return onClick;
};

describe('SectorNode3D — geometry selection', () => {
  it('renders a sphere for the default/unknown sector type', () => {
    renderNode({ sector: baseSector({ type: 'star' }) });
    expect(captured.sphere).toHaveLength(1);
    expect(captured.box).toHaveLength(0);
    expect(captured.cylinder).toHaveLength(0);
  });

  it('renders a box for an asteroid sector', () => {
    renderNode({ sector: baseSector({ type: 'asteroid' }) });
    expect(captured.box).toHaveLength(1);
    expect(captured.sphere).toHaveLength(0);
  });

  it('renders a cylinder for a wormhole sector', () => {
    renderNode({ sector: baseSector({ type: 'wormhole' }) });
    expect(captured.cylinder).toHaveLength(1);
    expect(captured.sphere).toHaveLength(0);
  });

  it('halves the geometry size at low LOD detail', () => {
    renderNode({ sector: baseSector({ type: 'normal' }), lodLevel: lod({ detail: 'low' }) });
    // normal config scale=1.0 -> size 0.5 at low detail -> Sphere args[0]=0.5
    expect(captured.sphere[0].args?.[0]).toBeCloseTo(0.5, 5);
  });
});

describe('SectorNode3D — color/opacity by knowledge state', () => {
  const materialProps = () => captured.sphere[captured.sphere.length - 1].children.props;

  it('colors a frontier (fog) contact violet at reduced opacity', () => {
    renderNode({ sector: baseSector(), knowledge: 'frontier' });
    const mat = materialProps();
    expect((mat.color as Color).getHexString()).toBe('7a52c9');
    expect(mat.opacity).toBeCloseTo(0.35, 5);
  });

  it('colors the current sector green at full opacity', () => {
    renderNode({ sector: baseSector(), isCurrent: true, knowledge: 'current' });
    const mat = materialProps();
    expect((mat.color as Color).getHexString()).toBe('3dff7a');
    expect(mat.opacity).toBe(1.0);
  });

  it('colors a reachable sector amber, brightening on hover', () => {
    const onClick = vi.fn();
    act(() => {
      root.render(
        <SectorNode3D
          sector={baseSector()}
          position={new Vector3(0, 0, 0)}
          isSelected={false}
          isCurrent={false}
          onClick={onClick}
          lodLevel={lod()}
          playerCount={0}
          knowledge="reachable"
        />
      );
    });
    const unhoveredHex = (materialProps().color as Color).getHexString();
    expect(unhoveredHex).toBe('ffb023');

    const group = container.querySelector('group') as HTMLElement;
    act(() => {
      group.dispatchEvent(new MouseEvent('pointerover', { bubbles: true }));
    });
    const hoveredHex = (materialProps().color as Color).getHexString();
    expect(hoveredHex).not.toBe('ffb023'); // lerped toward white
  });

  it('colors a selected (non-reachable, non-current) sector yellow', () => {
    renderNode({ sector: baseSector(), isSelected: true, knowledge: 'visited' });
    // isSelected check comes before the visited fallback in the component's
    // color memo, so a selected+visited sector still reads selected-yellow.
    const mat = materialProps();
    expect((mat.color as Color).getHexString()).toBe('ffe14d');
  });

  it('colors a known (charted, unvisited) sector dim slate at reduced opacity', () => {
    renderNode({ sector: baseSector(), knowledge: 'known' });
    const mat = materialProps();
    expect((mat.color as Color).getHexString()).toBe('3a4a63');
    expect(mat.opacity).toBeCloseTo(0.55, 5);
  });

  it('colors a visited sector steel blue at near-full opacity', () => {
    renderNode({ sector: baseSector(), knowledge: 'visited' });
    const mat = materialProps();
    expect((mat.color as Color).getHexString()).toBe('6aa8e8');
    expect(mat.opacity).toBeCloseTo(0.95, 5);
  });

  it('drops opacity for low-LOD, non-knowledge-overridden sectors', () => {
    renderNode({ sector: baseSector(), knowledge: 'reachable', lodLevel: lod({ detail: 'low' }) });
    // reachable's own opacity branch (not in the {frontier,known,visited}
    // set) falls through to the lodLevel.detail==='low' ? 0.7 : 1.0 branch.
    expect(materialProps().opacity).toBeCloseTo(0.7, 5);
  });
});

describe('SectorNode3D — player-count indicator', () => {
  it('omits the indicator when playerCount is 0', () => {
    renderNode({ sector: baseSector(), playerCount: 0 });
    expect(container.querySelector('[data-testid="text3d"]')).toBeNull();
  });

  it('omits the indicator when showEffects is false, even with players present', () => {
    renderNode({ sector: baseSector(), playerCount: 3, lodLevel: lod({ showEffects: false }) });
    expect(captured.text).toHaveLength(0);
  });

  it('shows the player-count label when players are present and showLabels is on', () => {
    renderNode({ sector: baseSector(), playerCount: 4 });
    expect(container.querySelector('[data-testid="text3d"]')?.textContent).toBe('4');
  });

  it('shows the indicator sphere without a label when showLabels is off', () => {
    renderNode({ sector: baseSector(), playerCount: 2, lodLevel: lod({ showLabels: false }) });
    expect(captured.text).toHaveLength(0);
    // The player-count marker sphere itself still renders (a raw <Sphere>
    // it shares the same mocked component as the main geometry, so two
    // Sphere calls total for a default 'normal' sector).
    expect(captured.sphere.length).toBeGreaterThanOrEqual(2);
  });
});

describe('SectorNode3D — screen-space label', () => {
  it('omits the label when showLabels is off', () => {
    renderNode({ sector: baseSector(), lodLevel: lod({ showLabels: false }) });
    expect(captured.html).toHaveLength(0);
  });

  it('omits the label at low LOD detail even with showLabels on', () => {
    renderNode({ sector: baseSector(), lodLevel: lod({ detail: 'low' }) });
    expect(captured.html).toHaveLength(0);
  });

  it('shows the sector name for a normal, non-frontier sector', () => {
    renderNode({ sector: baseSector({ name: 'Beta Watch' }), knowledge: 'visited' });
    expect(container.querySelector('.sector-node-label__name')?.textContent).toBe('Beta Watch');
    expect(container.querySelector('.sector-node-label--fog')).toBeNull();
  });

  it('withholds the name and applies the fog class for a frontier sector', () => {
    renderNode({ sector: baseSector({ name: 'Secret Reach' }), knowledge: 'frontier' });
    expect(container.querySelector('.sector-node-label__name')?.textContent).toBe('???');
    expect(container.querySelector('.sector-node-label--fog')).not.toBeNull();
  });
});

describe('SectorNode3D — special-formation indicators', () => {
  const formation = (over: Partial<SpecialFormationSummary> = {}): SpecialFormationSummary => ({
    id: 'f1',
    is_discovered: false,
    is_anchor: true,
    ...over,
  });

  it('renders nothing when the sector has no special_formations', () => {
    renderNode({ sector: baseSector({ special_formations: [] }) });
    // Only the geometry's own Text/Html calls would show up otherwise --
    // formation labels specifically never appear.
    expect(container.textContent).not.toContain('ANOMALY');
  });

  it('respects the showEffects/low-LOD gate even with formations present', () => {
    renderNode({
      sector: baseSector({ special_formations: [formation()] }),
      lodLevel: lod({ showEffects: false }),
    });
    expect(container.textContent).not.toContain('ANOMALY');
  });

  it('labels a single undiscovered formation generically as ANOMALY', () => {
    renderNode({ sector: baseSector({ special_formations: [formation()] }) });
    const labels = captured.text.map((t) => t.children);
    expect(labels).toContain('ANOMALY');
  });

  it('labels multiple undiscovered formations with a count', () => {
    renderNode({ sector: baseSector({ special_formations: [formation(), formation({ id: 'f2' })] }) });
    const labels = captured.text.map((t) => t.children);
    expect(labels).toContain('2 ANOMALIES');
  });

  it('names + types a discovered formation, uppercased and underscore-stripped', () => {
    renderNode({
      sector: baseSector({
        special_formations: [formation({ is_discovered: true, name: 'wyrm hollow', type: 'GRAVITY_WELL' })],
      }),
    });
    const labels = captured.text.map((t) => t.children);
    expect(labels).toContain('WYRM HOLLOW · GRAVITY WELL');
  });
});

describe('SectorNode3D — click handling', () => {
  it('fires onClick with the sector on a clickable, non-frontier node', () => {
    const sector = baseSector();
    const onClick = renderNode({ sector, knowledge: 'visited' });
    const group = container.querySelector('group') as HTMLElement;
    act(() => {
      group.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onClick).toHaveBeenCalledWith(sector);
  });

  it('never fires onClick when clickable is false', () => {
    const onClick = renderNode({ sector: baseSector(), knowledge: 'visited', clickable: false });
    const group = container.querySelector('group') as HTMLElement;
    act(() => {
      group.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onClick).not.toHaveBeenCalled();
  });

  it('never fires onClick on a frontier (fog) node even when clickable', () => {
    const onClick = renderNode({ sector: baseSector(), knowledge: 'frontier', clickable: true });
    const group = container.querySelector('group') as HTMLElement;
    act(() => {
      group.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('SectorNode3D — nebula chart tint (LEG-2590)', () => {
  const materialProps = () => captured.sphere[captured.sphere.length - 1].children.props;

  it('renders visited nebula sectors with canon color_hex instead of steel blue', () => {
    renderNode({
      sector: baseSector({ type: 'nebula', color_hex: '#DC143C' }),
      knowledge: 'visited',
    });
    const mat = materialProps();
    expect((mat.color as Color).getHexString()).toBe('dc143c');
  });

  it('falls back to steel blue for visited nebula when color_hex is absent', () => {
    renderNode({
      sector: baseSector({ type: 'nebula' }),
      knowledge: 'visited',
    });
    const mat = materialProps();
    expect((mat.color as Color).getHexString()).toBe('6aa8e8');
  });

  it('shows nebula type and field-strength range on hover when metadata is present', () => {
    renderNode({
      sector: baseSector({
        type: 'nebula',
        nebula_type: 'azure',
        quantum_field_strength: 65,
        color_hex: '#1E90FF',
      }),
      knowledge: 'visited',
    });
    const group = container.querySelector('group') as HTMLElement;
    act(() => {
      group.dispatchEvent(new MouseEvent('pointerover', { bubbles: true }));
    });
    expect(container.textContent).toContain('AZURE · 60–80');
  });

  it('withholds nebula hover on frontier fog nodes', () => {
    renderNode({
      sector: baseSector({
        type: 'nebula',
        nebula_type: 'crimson',
        quantum_field_strength: 90,
        color_hex: '#DC143C',
      }),
      knowledge: 'frontier',
    });
    const group = container.querySelector('group') as HTMLElement;
    act(() => {
      group.dispatchEvent(new MouseEvent('pointerover', { bubbles: true }));
    });
    expect(container.textContent).not.toContain('CRIMSON');
  });
});

describe('SectorNode3D — hover cursor', () => {
  it('sets a pointer cursor on hover and resets it on pointer-out, only when clickable', () => {
    renderNode({ sector: baseSector(), knowledge: 'visited', clickable: true });
    const group = container.querySelector('group') as HTMLElement;
    document.body.style.cursor = 'auto';

    act(() => {
      group.dispatchEvent(new MouseEvent('pointerover', { bubbles: true }));
    });
    expect(document.body.style.cursor).toBe('pointer');

    act(() => {
      group.dispatchEvent(new MouseEvent('pointerout', { bubbles: true }));
    });
    expect(document.body.style.cursor).toBe('auto');
  });

  it('never sets the pointer cursor when clickable is false', () => {
    renderNode({ sector: baseSector(), knowledge: 'visited', clickable: false });
    const group = container.querySelector('group') as HTMLElement;
    document.body.style.cursor = 'auto';
    act(() => {
      group.dispatchEvent(new MouseEvent('pointerover', { bubbles: true }));
    });
    expect(document.body.style.cursor).toBe('auto');
  });
});
