// @vitest-environment jsdom
/**
 * LanguageSwitcher — offline/API-down soft fallback + changeLanguage click.
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

// Avoid pulling real i18n bootstrap (Backend/LanguageDetector) — only need the map.
vi.mock('../../../i18n', () => ({
  SUPPORTED_LANGUAGES: {
    en: { name: 'English', nativeName: 'English' },
    es: { name: 'Spanish', nativeName: 'Español' },
  },
  default: {},
}));

import LanguageSwitcher from '../LanguageSwitcher';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('LanguageSwitcher', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockChangeLanguage.mockClear();
    mockI18n.language = 'en';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('offline')),
    );
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  it('soft-falls back to the static language list when fetch fails', async () => {
    await act(async () => {
      root.render(<LanguageSwitcher variant="full" />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    const btn = container.querySelector('.player-language-button') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(container.querySelector('.language-text')?.textContent).toBe('English');

    await act(async () => {
      btn.click();
      await flush();
    });
    const options = Array.from(container.querySelectorAll('.language-option')).map(
      (el) => el.textContent,
    );
    expect(options.some((t) => t?.includes('Español'))).toBe(true);
  });

  it('calls i18n.changeLanguage when a different option is chosen', async () => {
    await act(async () => {
      root.render(<LanguageSwitcher variant="full" showProgress={false} />);
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
    ) as HTMLButtonElement;
    expect(spanish).toBeTruthy();

    await act(async () => {
      spanish.click();
      await flush();
      await flush();
    });

    expect(mockChangeLanguage).toHaveBeenCalledWith('es');
  });
});
