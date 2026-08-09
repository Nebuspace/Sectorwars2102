// @vitest-environment jsdom
/**
 * PlayerMarker3D — LOD gate + label visibility (drei/three mocked).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Vector3 } from 'three';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('@react-three/drei', () => ({
  Text: ({ children }: { children?: React.ReactNode }) => (
    <span data-testid="player-label">{children}</span>
  ),
  Cone: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="player-cone">{children}</div>
  ),
}));

vi.mock('three', async () => {
  const actual = await vi.importActual<typeof import('three')>('three');
  return {
    ...actual,
    default: actual,
  };
});

import PlayerMarker3D from '../PlayerMarker3D';

const player = { user_id: 'u1', username: 'Nova', ship_type: 'scout' };

describe('PlayerMarker3D', () => {
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

  it('renders nothing at low LOD', async () => {
    await act(async () => {
      root.render(
        <PlayerMarker3D
          player={player}
          position={new Vector3(0, 0, 0)}
          lodLevel={{ detail: 'low', showLabels: true, showEffects: false }}
        />,
      );
    });
    expect(container.querySelector('[data-testid="player-cone"]')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('renders cone + label when medium LOD and showLabels', async () => {
    await act(async () => {
      root.render(
        <PlayerMarker3D
          player={player}
          position={new Vector3(1, 2, 3)}
          lodLevel={{ detail: 'medium', showLabels: true, showEffects: true }}
        />,
      );
    });
    expect(container.querySelector('[data-testid="player-cone"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="player-label"]')?.textContent).toBe('Nova');
  });

  it('omits the label when showLabels is false', async () => {
    await act(async () => {
      root.render(
        <PlayerMarker3D
          player={player}
          position={new Vector3(0, 0, 0)}
          lodLevel={{ detail: 'high', showLabels: false, showEffects: true }}
        />,
      );
    });
    expect(container.querySelector('[data-testid="player-cone"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="player-label"]')).toBeNull();
  });
});
