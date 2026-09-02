import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import LanguageSwitcher from './LanguageSwitcher';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: {
      language: 'en',
      changeLanguage: vi.fn().mockResolvedValue(undefined),
    },
  }),
}));

vi.mock('../../i18n', () => ({
  SUPPORTED_LANGUAGES: {
    en: { name: 'English', nativeName: 'English' },
    es: { name: 'Spanish', nativeName: 'Español' },
    zh: { name: 'Chinese (Simplified)', nativeName: '中文(简体)' },
    fr: { name: 'French', nativeName: 'Français' },
    pt: { name: 'Portuguese', nativeName: 'Português' },
  },
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3699 Soft-ORDER — LanguageSwitcher progress-fetch TypeError/Network Error densify.
 * LEG-3897 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('LanguageSwitcher typeErrorHonesty densify (LEG-3699)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on progress fetch without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<LanguageSwitcher />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Could not load translation progress|connection/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on progress fetch without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<LanguageSwitcher />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Could not load translation progress|connection/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with i18n/players view scope copy on progress fetch', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<LanguageSwitcher />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied/i);
    expect(alert).toMatch(/i18n|players view/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on progress fetch', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<LanguageSwitcher />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });
});
