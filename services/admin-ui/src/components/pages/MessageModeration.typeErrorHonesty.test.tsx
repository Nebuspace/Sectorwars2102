import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MessageModeration from './MessageModeration';
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
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    isConnected: true,
    subscribe: vi.fn(() => () => {}),
  }),
}));

const emptyMessages = { messages: [], total: 0, page: 1, limit: 20, pages: 1 };
const emptyBeacons = { beacons: [], total: 0, page: 1, limit: 20, pages: 1 };
const emptyStats = {
  total_messages: 0,
  messages_today: 0,
  messages_this_week: 0,
  flagged_messages: 0,
  most_active_senders: [],
};

function renderPage() {
  return render(
    <MemoryRouter>
      <MessageModeration />
    </MemoryRouter>,
  );
}

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
 * LEG-3487 Soft-ORDER — MessageModeration TypeError/Network Error honesty densify.
 * LEG-3903 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('MessageModeration typeErrorHonesty densify (LEG-3487)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on flagged messages load to honest fallback', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.reject(new Error('Network Error'));
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText('Failed to load the flagged-message review queue.'),
      ).toBeTruthy();
    });
    const text =
      screen.getByText('Failed to load the flagged-message review queue.')
        .textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on flagged messages load to honest fallback', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      if (url.startsWith('/api/v1/admin/beacons/flagged')) {
        return Promise.resolve({ data: emptyBeacons });
      }
      return Promise.resolve({ data: emptyMessages });
    });

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText('Failed to load the flagged-message review queue.'),
      ).toBeTruthy();
    });
    const text =
      screen.getByText('Failed to load the flagged-message review queue.')
        .textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('surfaces 403 with messaging moderation scope copy when flagged GET is denied', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.reject(axiosError(403));
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Access denied|messaging moderation scopes/i)).toBeTruthy();
    });
    const text =
      screen.getByText(/Access denied|messaging moderation scopes/i).textContent ?? '';
    expect(text).toMatch(/Access denied|messaging moderation scopes/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on flagged messages GET', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.reject(axiosError(429));
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
