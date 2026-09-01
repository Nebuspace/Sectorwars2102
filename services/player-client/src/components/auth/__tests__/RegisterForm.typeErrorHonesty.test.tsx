// @vitest-environment jsdom
/**
 * LEG-3696 Soft-ORDER — RegisterForm TypeError / Network Error densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const register = vi.fn();
const registerWithOAuth = vi.fn();

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ register, registerWithOAuth }),
}));

import RegisterForm, { formatRegisterError } from '../RegisterForm';

const NETWORK_FALLBACK =
  'Registration failed. Please check your connection and try again.';

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

async function fillValidForm(container: HTMLElement) {
  await setInputValue(container.querySelector('#username') as HTMLInputElement, 'newbie');
  await setInputValue(container.querySelector('#email') as HTMLInputElement, 'n@ex.com');
  await setInputValue(container.querySelector('#password') as HTMLInputElement, 'password1');
  await setInputValue(
    container.querySelector('#confirm-password') as HTMLInputElement,
    'password1',
  );
}

describe('RegisterForm TypeError densify (LEG-3696)', () => {
  it('formatRegisterError falls back on TypeError network collapse', () => {
    const text = formatRegisterError(new TypeError('Failed to fetch'));
    expect(text).toBe(NETWORK_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('falls back on axios Network Error / Failed to fetch', () => {
    expect(formatRegisterError(new Error('Network Error'))).toBe(NETWORK_FALLBACK);
    expect(formatRegisterError(new Error('Failed to fetch'))).toBe(NETWORK_FALLBACK);
    expect(formatRegisterError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });

  it('preserves structured API detail when not transport collapse', () => {
    expect(
      formatRegisterError({
        response: { data: { detail: 'Username already taken' } },
      }),
    ).toBe('Username already taken');
  });

  it('does not leak stack fragments or exception class names', () => {
    const err = new Error('TypeError: network down at fetch.ts:42');
    const text = formatRegisterError(err);
    expect(text).not.toMatch(/TypeError:/i);
    expect(text).not.toMatch(/fetch\.ts/i);
  });
});

describe('RegisterForm submit transport collapse densify (LEG-3696)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    register.mockReset();
    registerWithOAuth.mockReset();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(<RegisterForm />);
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('submit TypeError surfaces honest fallback without raw transport text', async () => {
    register.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await fillValidForm(container);

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
    register.mockRejectedValueOnce(new Error('Network Error'));
    await fillValidForm(container);

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
