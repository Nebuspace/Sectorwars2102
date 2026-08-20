import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import EventManagement from './EventManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => vi.fn().mockResolvedValue(false),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

describe('EventManagement scope errors (LEG-967)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 primary load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/')) {
        throw axiosError(403, 'Missing scope admin.events.manage');
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.events\.manage/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 primary load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/')) {
        throw axiosError(429);
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});
