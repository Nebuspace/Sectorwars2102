// @vitest-environment jsdom
/**
 * TerraformPanel — habitability readout + TerraformingPanel passthrough props.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const terraformProps: Record<string, unknown>[] = [];
vi.mock('../../planetary/TerraformingPanel', () => ({
  default: (props: Record<string, unknown>) => {
    terraformProps.push(props);
    return <div data-testid="terraforming-panel" />;
  },
}));

import TerraformPanel from '../TerraformPanel';

describe('TerraformPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    terraformProps.length = 0;
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

  it('shows rounded habitability /100 and forwards props to TerraformingPanel', async () => {
    const onUpdate = vi.fn();
    await act(async () => {
      root.render(
        <TerraformPanel
          planetId="p1"
          planetType="arid"
          playerCredits={500}
          habitabilityScore={72.6}
          onUpdate={onUpdate}
        />,
      );
    });

    expect(container.textContent).toContain('Terraform');
    expect(container.textContent).toContain('73/100');
    expect(container.querySelector('[data-testid="terraforming-panel"]')).toBeTruthy();
    expect(terraformProps[0]).toMatchObject({
      planetId: 'p1',
      planetType: 'arid',
      playerCredits: 500,
      habitabilityScore: 72.6,
      onUpdate,
    });
  });

  it('shows an em-dash readout when habitability is absent', async () => {
    await act(async () => {
      root.render(
        <TerraformPanel planetId="p2" playerCredits={0} onUpdate={vi.fn()} />,
      );
    });
    expect(container.textContent).toContain('—');
    expect(container.textContent).not.toContain('/100');
  });
});
