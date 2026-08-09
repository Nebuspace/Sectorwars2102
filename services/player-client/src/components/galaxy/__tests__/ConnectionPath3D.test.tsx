// @vitest-environment jsdom
/**
 * ConnectionPath3D — LOD cull, warp/tunnel color, curved vs straight points.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Vector3 } from 'three';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const lineProps: Record<string, unknown>[] = [];
vi.mock('@react-three/drei', () => ({
  Line: (props: Record<string, unknown>) => {
    lineProps.push(props);
    return <div data-testid="connection-line" />;
  },
}));

import ConnectionPath3D from '../ConnectionPath3D';

const lod = (detail: 'high' | 'medium' | 'low') => ({
  detail,
  showLabels: false,
  showEffects: false,
});

describe('ConnectionPath3D', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    lineProps.length = 0;
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

  it('culls long low-LOD links', async () => {
    await act(async () => {
      root.render(
        <ConnectionPath3D
          start={new Vector3(0, 0, 0)}
          end={new Vector3(100, 0, 0)}
          type="warp"
          lodLevel={lod('low')}
        />,
      );
    });
    expect(container.querySelector('[data-testid="connection-line"]')).toBeNull();
    expect(lineProps).toHaveLength(0);
  });

  it('draws a straight warp line at low LOD when short', async () => {
    await act(async () => {
      root.render(
        <ConnectionPath3D
          start={new Vector3(0, 0, 0)}
          end={new Vector3(10, 0, 0)}
          type="warp"
          lodLevel={lod('low')}
        />,
      );
    });
    expect(lineProps).toHaveLength(1);
    expect(lineProps[0].color).toBe('#4488ff');
    expect(lineProps[0].lineWidth).toBe(1);
    expect((lineProps[0].points as Vector3[]).length).toBe(2);
  });

  it('uses tunnel color/width and a mid bulge at higher LOD', async () => {
    await act(async () => {
      root.render(
        <ConnectionPath3D
          start={new Vector3(0, 0, 0)}
          end={new Vector3(20, 0, 0)}
          type="tunnel"
          lodLevel={lod('high')}
        />,
      );
    });
    expect(lineProps[0].color).toBe('#ff4444');
    expect(lineProps[0].lineWidth).toBe(3);
    expect((lineProps[0].points as Vector3[]).length).toBe(3);
  });
});
