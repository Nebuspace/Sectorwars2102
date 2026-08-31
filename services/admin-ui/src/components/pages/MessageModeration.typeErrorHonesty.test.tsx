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

/**
 * LEG-3487 Soft-ORDER — MessageModeration TypeError/Network Error honesty densify.
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
});
