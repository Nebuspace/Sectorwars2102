// @vitest-environment jsdom
/**
 * RegisterForm — auth-form coverage (WO-TESTCOV-PLAYER-AUTH-FORMS).
 * Client-side validation + register() handoff. AuthContext mocked.
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

import RegisterForm from '../RegisterForm';

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

describe('RegisterForm', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onRegisterSuccess: () => void;
  let switchToLogin: () => void;

  beforeEach(() => {
    register.mockReset();
    registerWithOAuth.mockReset();
    onRegisterSuccess = vi.fn<() => void>();
    switchToLogin = vi.fn<() => void>();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    act(() => {
      root.render(
        <RegisterForm onRegisterSuccess={onRegisterSuccess} switchToLogin={switchToLogin} />,
      );
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('rejects empty fields without calling register', async () => {
    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
    });
    expect(container.querySelector('.error-message')?.textContent).toBe(
      'Please fill in all fields',
    );
    expect(register).not.toHaveBeenCalled();
  });

  it('rejects mismatched passwords', async () => {
    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'newbie');
    await setInputValue(container.querySelector('#email') as HTMLInputElement, 'n@ex.com');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'password1');
    await setInputValue(
      container.querySelector('#confirm-password') as HTMLInputElement,
      'password2',
    );

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(
      'Passwords do not match',
    );
    expect(register).not.toHaveBeenCalled();
  });

  it('rejects passwords shorter than 8 characters', async () => {
    await setInputValue(container.querySelector('#username') as HTMLInputElement, 'newbie');
    await setInputValue(container.querySelector('#email') as HTMLInputElement, 'n@ex.com');
    await setInputValue(container.querySelector('#password') as HTMLInputElement, 'short');
    await setInputValue(
      container.querySelector('#confirm-password') as HTMLInputElement,
      'short',
    );

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(
      'Password must be at least 8 characters long',
    );
    expect(register).not.toHaveBeenCalled();
  });

  it('calls register and onRegisterSuccess on a valid submit', async () => {
    register.mockResolvedValueOnce(undefined);
    await fillValidForm(container);

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(register).toHaveBeenCalledWith('newbie', 'n@ex.com', 'password1');
    expect(onRegisterSuccess).toHaveBeenCalled();
  });

  it('surfaces server detail on registration failure', async () => {
    register.mockRejectedValueOnce({
      response: { data: { detail: 'Username already taken' } },
    });
    await fillValidForm(container);

    await act(async () => {
      container.querySelector('form')!.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      );
      await flush();
    });

    expect(container.querySelector('.error-message')?.textContent).toBe(
      'Username already taken',
    );
    expect(onRegisterSuccess).not.toHaveBeenCalled();
  });

  it('Sign In invokes switchToLogin', async () => {
    const signIn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Sign In'),
    ) as HTMLButtonElement;
    await act(async () => {
      signIn.click();
    });
    expect(switchToLogin).toHaveBeenCalled();
  });
});
