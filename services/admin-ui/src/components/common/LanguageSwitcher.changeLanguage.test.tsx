// @vitest-environment jsdom
/**
 * LEG-3236 Soft-ORDER — LanguageSwitcher changeLanguage error honesty.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LanguageSwitcher from './LanguageSwitcher';
import { api } from '../../utils/auth';

const mockChangeLanguage = vi.fn();

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: {
      language: 'en',
      changeLanguage: (...args: unknown[]) => mockChangeLanguage(...args),
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
    de: { name: 'German', nativeName: 'Deutsch' },
  },
}));

describe('LanguageSwitcher changeLanguage error honesty (LEG-3236)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    mockChangeLanguage.mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { overallCompletion: 100 } });
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows inline alert when changeLanguage rejects TypeError — no raw transport text in DOM', async () => {
    mockChangeLanguage.mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await user.click(screen.getByTitle('Change Language'));
    await waitFor(() => {
      expect(screen.getByText('Español')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Español'));

    await waitFor(() => {
      expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
    });

    const alerts = screen.getAllByRole('alert').map((el) => el.textContent ?? '');
    expect(alerts.some((t) => /Could not switch language/i.test(t))).toBe(true);
    expect(alerts.some((t) => /connection/i.test(t))).toBe(true);

    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('shows recoverable copy when changeLanguage rejects a non-TypeError', async () => {
    mockChangeLanguage.mockRejectedValue(new Error('bundle load failed'));
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await user.click(screen.getByTitle('Change Language'));
    await waitFor(() => {
      expect(screen.getByText('Français')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Français'));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Could not switch language/i);
    });

    expect(document.body.textContent ?? '').not.toMatch(/bundle load failed/i);
  });
});

/**
 * LEG-3543 Soft-ORDER — changeLanguage axios-shaped Network Error densify (invent=0).
 */
describe('LanguageSwitcher changeLanguage Network Error densify (LEG-3543)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    mockChangeLanguage.mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { overallCompletion: 100 } });
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows recoverable copy on axios-shaped Network Error — no raw transport text', async () => {
    mockChangeLanguage.mockRejectedValue(new Error('Network Error'));
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await user.click(screen.getByTitle('Change Language'));
    await waitFor(() => {
      expect(screen.getByText('Español')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Español'));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Could not switch language/i);
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });
});
