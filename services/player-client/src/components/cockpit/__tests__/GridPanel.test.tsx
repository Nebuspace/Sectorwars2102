// @vitest-environment jsdom
/**
 * GridPanel — placed/capacity readout + GridManager prop passthrough.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const gridProps: Record<string, unknown>[] = [];
vi.mock('../../planetary/GridManager', () => ({
  default: (props: Record<string, unknown>) => {
    gridProps.push(props);
    return <div data-testid="grid-manager" />;
  },
}));

import GridPanel from '../GridPanel';

describe('GridPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    gridProps.length = 0;
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

  it('shows placed/capacity readout and forwards props to GridManager', async () => {
    const onUpdate = vi.fn();
    await act(async () => {
      root.render(
        <GridPanel planetId="p1" playerCredits={200} placed={4} capacity={9} onUpdate={onUpdate} />,
      );
    });

    expect(container.textContent).toContain('Grid');
    expect(container.textContent).toContain('4/9');
    expect(container.querySelector('[data-testid="grid-manager"]')).toBeTruthy();
    expect(gridProps[0]).toMatchObject({
      planetId: 'p1',
      playerCredits: 200,
      onUpdate,
    });
  });

  it('falls back to x/9 when placed is unknown, and defaults capacity to 9', async () => {
    await act(async () => {
      root.render(<GridPanel planetId="p2" playerCredits={0} onUpdate={vi.fn()} />);
    });
    expect(container.textContent).toContain('x/9');

    await act(async () => {
      root.render(
        <GridPanel planetId="p2" playerCredits={0} placed={2} onUpdate={vi.fn()} />,
      );
    });
    expect(container.textContent).toContain('2/9');
  });
});
