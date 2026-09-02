// @vitest-environment jsdom
/**
 * LEG-3802 Soft-ORDER — FirstLoginContainer error banner typeErrorHonesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockUseFirstLogin = vi.fn();

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => mockUseFirstLogin(),
}));

vi.mock('../ShipSelection', () => ({
  default: () => <div data-testid="ship-selection-mock" />,
}));

vi.mock('../DialogueExchange', () => ({
  default: () => <div data-testid="dialogue-exchange-mock" />,
}));

vi.mock('../OutcomeDisplay', () => ({
  default: () => <div data-testid="outcome-display-mock" />,
}));

import FirstLoginContainer from '../FirstLoginContainer';

const baseContext = {
  isLoading: false,
  session: null,
  startSession: vi.fn(),
  resetError: vi.fn(),
  resetSession: vi.fn(),
  requiresFirstLogin: true,
  dialogueOutcome: null,
  dialogueHistory: [] as unknown[],
};

describe('FirstLoginContainer error banner typeErrorHonesty (LEG-3802)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockUseFirstLogin.mockReset();
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

  const mount = async () => {
    await act(async () => {
      root.render(<FirstLoginContainer />);
    });
  };

  it.each([
    ['TypeError fallback', 'Failed to start first login session.'],
    ['status check fallback', 'Failed to check first login status.'],
  ])('renders sanitized context error (%s) without transport leak', async (_label, errorText) => {
    mockUseFirstLogin.mockReturnValue({
      ...baseContext,
      error: errorText,
    });

    await mount();

    const banner = container.querySelector('.error-message');
    expect(banner).toBeTruthy();
    expect(banner?.textContent).toContain(errorText);
    expect(banner?.textContent).not.toMatch(/Failed to fetch/i);
    expect(banner?.textContent).not.toMatch(/TypeError/i);
    expect(banner?.textContent).not.toMatch(/^Network Error$/i);
  });

  it('renders ERR_NETWORK prose without raw axios transport tokens', async () => {
    mockUseFirstLogin.mockReturnValue({
      ...baseContext,
      error: 'Network error. Please check your connection.',
    });

    await mount();

    const banner = container.querySelector('.error-message');
    expect(banner?.textContent).toContain('Network error. Please check your connection.');
    expect(banner?.textContent).not.toMatch(/Failed to fetch/i);
    expect(banner?.textContent).not.toMatch(/TypeError/i);
  });
});
