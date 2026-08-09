// @vitest-environment jsdom
/**
 * TurnsIcon — aria-label/title, size, aria-hidden override.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { TurnsIcon } from '../TurnsIcon';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('TurnsIcon', () => {
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

  it('renders a labeled fast-forward SVG at default 1em size', async () => {
    await act(async () => {
      root.render(<TurnsIcon />);
    });
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('role')).toBe('img');
    expect(svg?.getAttribute('aria-label')).toBe('Turns');
    expect(svg?.getAttribute('width')).toBe('1em');
    expect(svg?.getAttribute('height')).toBe('1em');
    expect(container.querySelector('title')?.textContent).toBe('Turns');
    expect(container.querySelectorAll('polygon').length).toBe(2);
  });

  it('accepts numeric size and decorative aria-hidden override', async () => {
    await act(async () => {
      root.render(<TurnsIcon size={14} className="hud-green" aria-hidden="true" />);
    });
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('14');
    expect(svg?.getAttribute('height')).toBe('14');
    expect(svg?.getAttribute('class')).toBe('hud-green');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
  });
});
