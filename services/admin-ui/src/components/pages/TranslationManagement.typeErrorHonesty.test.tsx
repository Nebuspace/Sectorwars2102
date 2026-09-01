import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TranslationManagement from './TranslationManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { is_admin: true }, token: 'tok' }),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const sampleLanguage = {
  code: 'en',
  name: 'English',
  nativeName: 'English',
  direction: 'ltr',
  isActive: true,
  completionPercentage: 80,
};

const sampleProgress = {
  language: 'en',
  overallCompletion: 80,
  totalKeys: 50,
  translatedKeys: 40,
  namespaces: {
    common: {
      totalKeys: 50,
      translatedKeys: 40,
      verifiedKeys: 35,
      completionPercentage: 80,
      lastUpdated: '2026-01-01T00:00:00Z',
    },
  },
};

function mockSuccessfulLanguagesLoad() {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url === '/api/v1/i18n/admin/languages/all') {
      return Promise.resolve({ data: [sampleLanguage] });
    }
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
}

async function openSaveKeyFlow() {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url === '/api/v1/i18n/admin/languages/all') {
      return Promise.resolve({ data: [sampleLanguage] });
    }
    if (url === '/api/v1/i18n/admin/progress/en') {
      return Promise.resolve({ data: sampleProgress });
    }
    if (url === '/api/v1/i18n/en/common') {
      return Promise.resolve({ data: { buttons: { save: 'Save' } } });
    }
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });

  render(<TranslationManagement />);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'View progress' })).toBeTruthy();
  });

  fireEvent.click(screen.getByRole('button', { name: 'View progress' }));

  await waitFor(() => {
    expect(screen.getByText('Progress: en')).toBeTruthy();
  });

  fireEvent.click(screen.getByRole('button', { name: 'Browse' }));

  await waitFor(() => {
    expect(screen.getByText('buttons.save')).toBeTruthy();
  });

  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

  await waitFor(() => {
    expect(screen.getByText('Edit Translation Key')).toBeTruthy();
  });
}

/**
 * LEG-3665 Soft-ORDER — TranslationManagement TypeError/Network Error densify.
 */
describe('TranslationManagement typeErrorHonesty densify (LEG-3665)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('collapses axios Network Error on languages load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load languages/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load languages/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on languages load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load languages/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load languages/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on progress load without leaking raw transport text', async () => {
    mockSuccessfulLanguagesLoad();

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'View progress' })).toBeTruthy();
    });

    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/i18n/admin/languages/all') {
        return Promise.resolve({ data: [sampleLanguage] });
      }
      if (url === '/api/v1/i18n/admin/progress/en') {
        return Promise.reject(new Error('Network Error'));
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    fireEvent.click(screen.getByRole('button', { name: 'View progress' }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load progress for "en"/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load progress for "en"/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on progress load without leaking transport text', async () => {
    mockSuccessfulLanguagesLoad();

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'View progress' })).toBeTruthy();
    });

    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/i18n/admin/languages/all') {
        return Promise.resolve({ data: [sampleLanguage] });
      }
      if (url === '/api/v1/i18n/admin/progress/en') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    fireEvent.click(screen.getByRole('button', { name: 'View progress' }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load progress for "en"/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load progress for "en"/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on namespace keys load without leaking raw transport text', async () => {
    mockSuccessfulLanguagesLoad();

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'View progress' })).toBeTruthy();
    });

    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/i18n/admin/languages/all') {
        return Promise.resolve({ data: [sampleLanguage] });
      }
      if (url === '/api/v1/i18n/admin/progress/en') {
        return Promise.resolve({ data: sampleProgress });
      }
      if (url === '/api/v1/i18n/en/common') {
        return Promise.reject(new Error('Network Error'));
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    fireEvent.click(screen.getByRole('button', { name: 'View progress' }));

    await waitFor(() => {
      expect(screen.getByText('Progress: en')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load keys for "common"/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load keys for "common"/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on namespace keys load without leaking transport text', async () => {
    mockSuccessfulLanguagesLoad();

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'View progress' })).toBeTruthy();
    });

    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/i18n/admin/languages/all') {
        return Promise.resolve({ data: [sampleLanguage] });
      }
      if (url === '/api/v1/i18n/admin/progress/en') {
        return Promise.resolve({ data: sampleProgress });
      }
      if (url === '/api/v1/i18n/en/common') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    fireEvent.click(screen.getByRole('button', { name: 'View progress' }));

    await waitFor(() => {
      expect(screen.getByText('Progress: en')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Browse' }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load keys for "common"/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load keys for "common"/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on save-key POST without leaking raw transport text', async () => {
    const user = userEvent.setup();
    await openSaveKeyFlow();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    await user.click(screen.getByRole('button', { name: /^Save$/i }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to save translation key/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on save-key POST without leaking transport text', async () => {
    const user = userEvent.setup();
    await openSaveKeyFlow();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    await user.click(screen.getByRole('button', { name: /^Save$/i }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to save translation key/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });
});
