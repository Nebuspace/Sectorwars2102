// @vitest-environment jsdom
/**
 * LEG-3695 Soft-ORDER — LoginForm TypeError / Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { MFARequiredError, login, loginWithOAuth } = vi.hoisted(() => {
  class MFARequiredError extends Error {
    constructor(message = 'MFA required') {
      super(message);
      this.name = 'MFARequiredError';
    }
  }
  return {
    MFARequiredError,
    login: vi.fn(),
    loginWithOAuth: vi.fn(),
  };
});

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ login, loginWithOAuth }),
  MFARequiredError,
}));

import LoginForm, { formatLoginError } from '../LoginForm';

const NETWORK_FALLBACK = 'Unable to sign in. Please check your connection and try again.';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function setInputValue(el: HTMLInputElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('LoginForm TypeError densify (LEG-3695)', () => {
  it('formatLoginError falls back on TypeError network collapse', () => {
    const text = formatLoginError(new TypeError('Failed to fetch'));
    expect(text).toBe(NETWORK_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatLoginError(new Error('Network Error'))).toBe(NETWORK_FALLBACK);
    expect(formatLoginError(new Error('Failed to fetch'))).toBe(NETWORK_FALLBACK);
    expect(formatLoginError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('collapses malformed JSON / parse noise without leaking raw exception text', () => {
    expect(formatLoginError(new Error('Unexpected token < in JSON at position 0'))).toBe(
      NETWORK_FALLBACK,
    );
    expect(formatLoginError(new SyntaxError('JSON.parse: unexpected character'))).toBe(
      NETWORK_FALLBACK,
    );
  });

  it('preserves non-generic server detail when not transport collapse', () => {
    expect(formatLoginError(new Error('account_locked'))).toBe('account_locked');
  });

  it('formatLoginError uses MFA fallback for non-network verify failures', () => {
    expect(formatLoginError(new Error('bad code'), { mfa: true })).toBe(
      'Invalid authentication code',
    );
  });

  it('formatLoginError uses network fallback for MFA path on transport collapse', () => {
    expect(formatLoginError(new TypeError('Failed to fetch'), { mfa: true })).toBe(
      NETWORK_FALLBACK,
    );
  });
});

describe('LoginForm submit transport collapse densify (LEG-3695)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    login.mockReset();
    loginWithOAuth.mockReset();

    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        origin: 'http://localhost:5173',
        href: '/',
      },
    });
    Object.defineProperty(window.location, 'href', {
      configurable: true,
      get: () => '/',
      set: vi.fn(),
    });

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(<LoginForm />);
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('submit TypeError surfaces honest fallback without raw transport text', async () => {
    login.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'commander');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'secret-pass');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(NETWORK_FALLBACK);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('submit Network Error surfaces honest fallback without raw transport text', async () => {
    login.mockRejectedValueOnce(new Error('Network Error'));

    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'commander');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'secret-pass');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(NETWORK_FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('MFA verify Network Error surfaces honest fallback without raw transport text', async () => {
    login
      .mockRejectedValueOnce(new MFARequiredError())
      .mockRejectedValueOnce(new Error('Network Error'));

    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'commander');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'secret-pass');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    await setInputValue(container.querySelector('#mfa-code') as HTMLInputElement, '123456');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(NETWORK_FALLBACK);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });
});
