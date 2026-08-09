// @vitest-environment jsdom
/**
 * StarField — deterministic BufferGeometry (shell vs volume), stable remount.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type CaptureBag = { positions: Float32Array[] };
const capture: CaptureBag = ((globalThis as unknown as { __starFieldCapture?: CaptureBag })
  .__starFieldCapture ??= { positions: [] });

vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  class CapturingBufferGeometry extends actual.BufferGeometry {
    setAttribute(
      name: string,
      attribute: import('three').BufferAttribute | import('three').InterleavedBufferAttribute,
    ) {
      if (name === 'position' && 'array' in attribute) {
        const bag = (globalThis as unknown as { __starFieldCapture: CaptureBag })
          .__starFieldCapture;
        bag.positions.push((attribute.array as Float32Array).slice());
      }
      return super.setAttribute(name, attribute);
    }
  }
  return {
    ...actual,
    BufferGeometry: CapturingBufferGeometry,
  };
});

import StarField from '../StarField';

describe('StarField', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    capture.positions.length = 0;
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

  it('builds count*3 positions and remounts identically', async () => {
    await act(async () => {
      root.render(<StarField count={12} radius={50} shell />);
    });
    expect(capture.positions.length).toBeGreaterThanOrEqual(1);
    expect(capture.positions[0]).toHaveLength(36);

    const first = capture.positions[0].slice();
    await act(async () => {
      root.render(<StarField count={12} radius={50} shell />);
    });
    const last = capture.positions[capture.positions.length - 1];
    expect(Array.from(last)).toEqual(Array.from(first));
  });

  it('places shell stars near the outer radius', async () => {
    await act(async () => {
      root.render(<StarField count={8} radius={100} shell />);
    });
    const pos = capture.positions[0];
    for (let i = 0; i < 8; i++) {
      const r = Math.hypot(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
      expect(r).toBeGreaterThanOrEqual(92);
      expect(r).toBeLessThanOrEqual(100.01);
    }
  });

  it('fills the volume when shell is false', async () => {
    await act(async () => {
      root.render(<StarField count={8} radius={100} shell={false} />);
    });
    const pos = capture.positions[0];
    const radii = Array.from({ length: 8 }, (_, i) =>
      Math.hypot(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]),
    );
    expect(Math.min(...radii)).toBeLessThan(92);
  });
});
