// @vitest-environment jsdom
/**
 * MineIcon — aria-label/title, size, spike count, aria-hidden override.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { MineIcon } from '../MineIcon';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('MineIcon', () => {
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

  it('renders a labeled naval-mine SVG (body + 8 spikes) at default 1em', async () => {
    await act(async () => {
      root.render(<MineIcon />);
    });
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('role')).toBe('img');
    expect(svg?.getAttribute('aria-label')).toBe('Mine');
    expect(svg?.getAttribute('width')).toBe('1em');
    expect(container.querySelector('title')?.textContent).toBe('Mine');
    expect(container.querySelectorAll('circle').length).toBe(1);
    expect(container.querySelectorAll('line').length).toBe(8);
  });

  it('accepts size and decorative aria-hidden override', async () => {
    await act(async () => {
      root.render(<MineIcon size="0.8rem" className="hud-amber" aria-hidden="true" />);
    });
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('0.8rem');
    expect(svg?.getAttribute('class')).toBe('hud-amber');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
  });
});
