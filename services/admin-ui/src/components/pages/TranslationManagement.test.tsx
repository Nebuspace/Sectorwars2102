import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import TranslationManagement from './TranslationManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
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
});
