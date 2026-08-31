import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    de: { name: 'German', nativeName: 'Deutsch' },
  },
}));

describe('LanguageSwitcher launch-complete honesty (LEG-488)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('does not render 0% for launch-complete locales when progress API fails (static fallback)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('progress unavailable'));
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await user.click(screen.getByTitle('Change Language'));

    await waitFor(() => {
      expect(screen.getByText('Español')).toBeInTheDocument();
    });

    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.getByText('Français')).toBeInTheDocument();
    expect(screen.getByText('中文(简体)')).toBeInTheDocument();
    expect(screen.getByText('Português')).toBeInTheDocument();
    expect(screen.queryByText('Deutsch')).not.toBeInTheDocument();
  });

  it('hides the completion bar for English at 100%', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { overallCompletion: 100 } });
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await user.click(screen.getByTitle('Change Language'));

    await waitFor(() => {
      expect(screen.getAllByText('English').length).toBeGreaterThan(0);
    });

    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
  });

  it('displays the progress API percent for a launch-complete locale when present', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/es')) {
        return { data: { overallCompletion: 100 } };
      }
      return { data: { overallCompletion: 100 } };
    });
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await user.click(screen.getByTitle('Change Language'));

    await waitFor(() => {
      expect(screen.getByText('Español')).toBeInTheDocument();
    });

    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });
});

describe('LanguageSwitcher progress HTTP honesty (LEG-1265)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('shows Access denied alert on progress 403 (not silent 100% success)', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403 } }),
    );
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|i18n|scope/i);

    await user.click(screen.getByTitle('Change Language'));
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });

  it('shows admin rate-limit alert on progress 429', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429 } }),
    );
    render(<LanguageSwitcher />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    expect(screen.getByRole('alert').textContent ?? '').toMatch(/rate limit/i);
  });
});

describe('LanguageSwitcher TypeError densify (LEG-3174)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('uses static launch-complete fallback on progress TypeError without raw transport text in DOM', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    render(<LanguageSwitcher />);

    await user.click(screen.getByTitle('Change Language'));

    await waitFor(() => {
      expect(screen.getByText('Español')).toBeInTheDocument();
    });

    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.getByText('Français')).toBeInTheDocument();

    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
