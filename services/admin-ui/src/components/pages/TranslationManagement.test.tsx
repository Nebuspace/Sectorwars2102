import { describe, it, expect, vi, beforeEach } from 'vitest';
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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

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

function mockSuccessfulLoad() {
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
}

async function openSaveKeyFlow() {
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

describe('TranslationManagement scope errors (LEG-925)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows scope-aware copy when languages fetch returns 403', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.i18n.manage' },
        },
      }),
    );

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByText(/admin\.i18n\.manage|Missing scope/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429 } }),
    );

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces honest fallback on languages load TypeError/network collapse (LEG-3024)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<TranslationManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load languages/i)).toBeTruthy();
    });

    const text = screen.getByText(/Failed to load languages/i).textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });
});

describe('TranslationManagement save-key mutation errors (LEG-2626)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastError.mockReset();
    mockSuccessfulLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces formatAdminApiError on save-key POST 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.i18n.manage'),
    );

    await openSaveKeyFlow();
    await user.click(screen.getByRole('button', { name: /^Save$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/i18n/admin/translation/en/common',
        expect.objectContaining({ key: 'buttons.save' }),
      );
    });

    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/Missing scope admin\.i18n\.manage/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to save translation key');
  });

  it('shows rate-limit copy on save-key POST 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await openSaveKeyFlow();
    await user.click(screen.getByRole('button', { name: /^Save$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Failed to save translation key');
  });
});
