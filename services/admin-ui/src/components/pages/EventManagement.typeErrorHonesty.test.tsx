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
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

/**
 * LEG-3624 Soft-ORDER — EventManagement TypeError/Network Error honesty densify.
 */
describe('EventManagement typeErrorHonesty densify (LEG-3624)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on primary events load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/')) {
        throw new Error('Network Error');
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch event data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to fetch event data/i).textContent ?? '';
    expect(text).toMatch(/Failed to fetch event data/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on primary events load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch event data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to fetch event data/i).textContent ?? '';
    expect(text).toMatch(/Failed to fetch event data/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toBe('Failed to fetch');
  });
});
