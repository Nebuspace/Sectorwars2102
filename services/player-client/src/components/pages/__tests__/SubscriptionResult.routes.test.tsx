// @vitest-environment jsdom
/**
 * LEG-24 — PayPal return routes mount SubscriptionResult (not MainApp catch-all).
 */
import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRoot, Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../SubscriptionResult', () => ({
  default: () => <div data-testid="subscription-result">SubscriptionResult</div>,
}));

describe('LEG-24 subscription routes', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('App.tsx wires success and cancelled routes to SubscriptionResult', () => {
    const src = readFileSync(resolve(__dirname, '../../../App.tsx'), 'utf8');
    expect(src).toMatch(
      /import SubscriptionResult from ['"]\.\/components\/pages\/SubscriptionResult['"]/
    );
    expect(src).toContain('path="/subscription/success"');
    expect(src).toContain('path="/subscription/cancelled"');
    expect(src).toContain('element={<SubscriptionResult />}');
  });

  const renderAt = async (path: string) => {
    const SubscriptionResult = (await import('../SubscriptionResult')).default;
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/subscription/success" element={<SubscriptionResult />} />
            <Route path="/subscription/cancelled" element={<SubscriptionResult />} />
            <Route path="*" element={<div data-testid="main-app-fallback">MainApp</div>} />
          </Routes>
        </MemoryRouter>
      );
    });
  };

  it('mounts SubscriptionResult at /subscription/success', async () => {
    await renderAt('/subscription/success');
    expect(container.querySelector('[data-testid="subscription-result"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="main-app-fallback"]')).toBeNull();
  });

  it('mounts SubscriptionResult at /subscription/cancelled', async () => {
    await renderAt('/subscription/cancelled');
    expect(container.querySelector('[data-testid="subscription-result"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="main-app-fallback"]')).toBeNull();
  });
});
