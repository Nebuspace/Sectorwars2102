// @vitest-environment jsdom
/**
 * LEG-3747 Soft-ORDER — LanguageSwitcher TypeError / network-collapse densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockChangeLanguage = vi.fn(async (code: string) => {
  mockI18n.language = code;
});
const mockI18n = { language: 'en', changeLanguage: mockChangeLanguage };

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ i18n: mockI18n }),
  initReactI18next: { type: '3rdParty', init: () => undefined },
}));

vi.mock('../../../i18n', () => ({
  SUPPORTED_LANGUAGES: {
    en: { name: 'English', nativeName: 'English' },
    es: { name: 'Spanish', nativeName: 'Español' },
    fr: { name: 'French', nativeName: 'Français' },
    zh: { name: 'Chinese (Simplified)', nativeName: '中文(简体)' },
    pt: { name: 'Portuguese', nativeName: 'Português' },
    de: { name: 'German', nativeName: 'Deutsch' },
  },
  default: {},
}));

import LanguageSwitcher from '../LanguageSwitcher';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('LanguageSwitcher TypeError densify (LEG-3747)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockChangeLanguage.mockClear();
    mockI18n.language = 'en';
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it('TypeError Failed to fetch uses static fallback without leaking transport strings', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await act(async () => {
      root.render(<LanguageSwitcher variant="full" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.querySelector('.language-text')?.textContent).toBe('English');
    expect(errorSpy).not.toHaveBeenCalled();

    await act(async () => {
      (container.querySelector('.player-language-button') as HTMLButtonElement).click();
      await flush();
    });

    const options = Array.from(container.querySelectorAll('.language-option')).map(
      (el) => el.textContent,
    );
    expect(options.some((t) => t?.includes('中文'))).toBe(true);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
  });

  it('axios-style Network Error uses static fallback without leaking transport strings', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network Error')));

    await act(async () => {
      root.render(<LanguageSwitcher variant="full" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    expect(container.textContent).not.toMatch(/Network Error/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.querySelector('.language-text')?.textContent).toBe('English');
    expect(errorSpy).not.toHaveBeenCalled();

    await act(async () => {
      (container.querySelector('.player-language-button') as HTMLButtonElement).click();
      await flush();
    });

    const options = Array.from(container.querySelectorAll('.language-option')).map(
      (el) => el.textContent,
    );
    expect(options.some((t) => t?.includes('Español'))).toBe(true);
    expect(container.textContent).not.toMatch(/Network Error/i);
  });

  it('non-TypeError Failed to fetch string uses static fallback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Failed to fetch')));

    await act(async () => {
      root.render(<LanguageSwitcher variant="full" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    expect(container.querySelector('.language-text')?.textContent).toBe('English');
  });

  it('offline fallback shows canon STATIC_COMPLETION_PERCENT for German (26%)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await act(async () => {
      root.render(<LanguageSwitcher variant="full" showProgress />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await act(async () => {
      (container.querySelector('.player-language-button') as HTMLButtonElement).click();
      await flush();
    });

    const spanish = Array.from(container.querySelectorAll('.language-option')).find((el) =>
      el.textContent?.includes('Español'),
    );
    expect(spanish?.querySelector('.completion-text')).toBeNull();

    const german = Array.from(container.querySelectorAll('.language-option')).find((el) =>
      el.textContent?.includes('Deutsch'),
    );
    expect(german?.querySelector('.completion-text')?.textContent).toBe('26%');
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
  });
});
