// @vitest-environment jsdom
/**
 * LoginForm — auth-form coverage (WO-TESTCOV-PLAYER-AUTH-FORMS).
 *
 * Pins the MFA prompt seam wired by WO-FIX-MFA-BYPASS-LOGIN-ROUTES:
 * MFARequiredError reveals the authenticator field; Verify retries login
 * with the code; cancel restores the username/password step. Test-only —
 * AuthContext is mocked; no live auth/MFA logic is exercised here.
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
import LoginForm from '../LoginForm';

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

describe('LoginForm', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onLoginSuccess: () => void;
  let switchToRegister: () => void;
  let hrefSetter: (v: string) => void;

  beforeEach(() => {
    login.mockReset();
    loginWithOAuth.mockReset();
    onLoginSuccess = vi.fn<() => void>();
    switchToRegister = vi.fn<() => void>();
    hrefSetter = vi.fn<(v: string) => void>();


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
      set: hrefSetter,
    });

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(
        <LoginForm onLoginSuccess={onLoginSuccess} switchToRegister={switchToRegister} />,
      );
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('rejects empty username/password without calling login', async () => {
    const form = container.querySelector('form')!;
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(
      'Please enter both username and password',
    );
    expect(login).not.toHaveBeenCalled();
  });

  it('calls login and redirects on a successful non-MFA login', async () => {
    login.mockResolvedValueOnce(undefined);

    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'commander');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'secret-pass');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(login).toHaveBeenCalledWith('commander', 'secret-pass', undefined);
    expect(onLoginSuccess).toHaveBeenCalled();
    expect(hrefSetter).toHaveBeenCalledWith('/game');
  });

  it('reveals the MFA code field when login throws MFARequiredError', async () => {
    login.mockRejectedValueOnce(new MFARequiredError());

    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'commander');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'secret-pass');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(container.querySelector('#mfa-code')).toBeTruthy();
    expect(container.querySelector('#username')).toBeNull();
    expect(container.querySelector('#password')).toBeNull();
    expect(container.querySelector('.login-button')?.textContent).toBe('Verify');
    expect(container.querySelector('.error-message')).toBeNull();
    expect(hrefSetter).not.toHaveBeenCalled();
  });

  it('retries login with the MFA code on Verify', async () => {
    login
      .mockRejectedValueOnce(new MFARequiredError())
      .mockResolvedValueOnce(undefined);

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

    expect(login).toHaveBeenLastCalledWith('commander', 'secret-pass', '123456');
    expect(hrefSetter).toHaveBeenCalledWith('/game');
  });

  it('shows Invalid authentication code when MFA verify fails', async () => {
    login
      .mockRejectedValueOnce(new MFARequiredError())
      .mockRejectedValueOnce(new Error('bad code'));

    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'commander');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'secret-pass');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    await setInputValue(container.querySelector('#mfa-code') as HTMLInputElement, '000000');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(
      'Invalid authentication code',
    );
    expect(hrefSetter).not.toHaveBeenCalled();
  });

  it('Use a different account restores the username/password step', async () => {
    login.mockRejectedValueOnce(new MFARequiredError());

    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'commander');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'secret-pass');

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    const cancel = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Use a different account'),
    ) as HTMLButtonElement;
    await act(async () => {
      cancel.click();
    });

    expect(container.querySelector('#username')).toBeTruthy();
    expect(container.querySelector('#password')).toBeTruthy();
    expect(container.querySelector('#mfa-code')).toBeNull();
    expect(container.querySelector('.login-button')?.textContent).toBe('Play Now');
  });

  it('Create Account invokes switchToRegister', async () => {
    const create = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Create Account'),
    ) as HTMLButtonElement;
    await act(async () => {
      create.click();
    });
    expect(switchToRegister).toHaveBeenCalled();
  });
});
