// @vitest-environment jsdom
/**
 * LEG-3779 Soft-ORDER — LabShell typeErrorHonesty.
 * Dev geometry harness: mocked GameContext only — no lab/session API fetch paths.
 * LEG-4058 Soft-ORDER — HTTP 403/429 densify (invent=0): harness must not surface raw status copy.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../GameLayout', () => ({
  default: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="game-layout-mock">{children}</div>
  ),
}));

import LabShell from '../LabShell';

describe('LabShell TypeError densify (LEG-3779)', () => {
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

  it('renders harness chrome without surfacing transport error strings', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/lab/shell?mode=flight']}>
          <LabShell />
        </MemoryRouter>,
      );
    });

    expect(container.querySelector('[data-testid="lab-shell-ready"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="lab-shell-deck"]')).toBeTruthy();
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/Network Error/i);
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(container.textContent).not.toMatch(/\b429\b/);
    expect(container.textContent).not.toMatch(/API Error/i);
  });

  it('harness chrome densifies 403/429 — no raw status strings in DOM (LEG-4058)', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/lab/shell?mode=station']}>
          <LabShell />
        </MemoryRouter>,
      );
    });

    expect(container.querySelector('[data-testid="lab-shell-ready"]')).toBeTruthy();
    expect(container.textContent).not.toMatch(/Access denied/i);
    expect(container.textContent).not.toMatch(/rate limit/i);
    expect(container.textContent).not.toMatch(/\b403\b/);
    expect(container.textContent).not.toMatch(/\b429\b/);
    expect(container.textContent).not.toMatch(/API Error: 403/i);
    expect(container.textContent).not.toMatch(/API Error: 429/i);
  });
});
