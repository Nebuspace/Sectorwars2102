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

/**
 * LEG-3699 Soft-ORDER — LanguageSwitcher progress-fetch TypeError/Network Error densify.
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
});
