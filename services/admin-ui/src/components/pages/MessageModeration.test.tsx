import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MessageModeration from './MessageModeration';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: toastSuccess, error: toastError }),
  useConfirm: () => confirmMock,
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

const message = {
  id: 'm1',
  sender_id: 'sender-uuid',
  recipient_id: 'recip-uuid',
  team_id: null,
  subject: 'Hello',
  content: 'This is flagged content',
  sent_at: '2026-08-01T00:00:00Z',
  read_at: null,
  message_type: 'direct',
  priority: 'normal',
  thread_id: null,
  reply_to_id: null,
  flagged: true,
  is_read: false,
  sender_name: 'Alice',
};

const beacon = {
  id: 'b1',
  region_id: 'r1',
  sector_id: 42,
  deployer_player_id: 'deployer-uuid',
  deployer_nickname: 'Bob',
  message: 'Abusive beacon text',
  preview: 'Abusive...',
  deployed_at: '2026-08-01T00:00:00Z',
  flagged: true,
};

function mockLoad({
  messages = emptyMessages,
  stats = emptyStats,
  beacons = emptyBeacons,
}: { messages?: any; stats?: any; beacons?: any } = {}) {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (url.startsWith('/api/v1/admin/messages/flagged')) {
      return Promise.resolve({ data: messages });
    }
    if (url === '/api/v1/admin/messages/stats') {
      return Promise.resolve({ data: stats });
    }
    if (url.startsWith('/api/v1/admin/beacons/flagged')) {
      return Promise.resolve({ data: beacons });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

describe('MessageModeration', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
  });

  it('shows honest empty states when nothing is flagged', async () => {
    mockLoad();
    render(<MessageModeration />);

    await waitFor(() => {
      expect(screen.getByText('No flagged messages.')).toBeTruthy();
    });
    expect(screen.getByText('No flagged sector beacons.')).toBeTruthy();
  });

  it('renders flagged messages and beacons from the real endpoints', async () => {
    mockLoad({
      messages: { ...emptyMessages, messages: [message], total: 1 },
      beacons: { ...emptyBeacons, beacons: [beacon], total: 1 },
      stats: { ...emptyStats, total_messages: 10, flagged_messages: 1 },
    });

    render(<MessageModeration />);

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeTruthy();
    });
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/messages/flagged?page=1');
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/messages/stats');
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/beacons/flagged?page=1');
    expect(screen.getByText('Bob')).toBeTruthy();
    expect(screen.getByText('42')).toBeTruthy();
  });

  it('falls back to a truncated deployer id when the beacon has no nickname', async () => {
    mockLoad({
      beacons: {
        ...emptyBeacons,
        beacons: [{ ...beacon, deployer_nickname: null, deployer_player_id: 'abcdef12345678' }],
        total: 1,
      },
    });

    render(<MessageModeration />);

    await waitFor(() => {
      expect(screen.getByText('abcdef12345678')).toBeTruthy();
    });
  });

  it('shows independent error banners when one of the three GETs fails without blocking the others', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.reject(new Error('boom'));
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    render(<MessageModeration />);

    await waitFor(() => {
      expect(
        screen.getByText('Failed to load the flagged-message review queue.'),
      ).toBeTruthy();
    });
    // Beacons section still rendered its honest empty state, not blocked by the message failure.
    expect(screen.getByText('No flagged sector beacons.')).toBeTruthy();
  });

  it('deletes a message only after the confirm dialog is accepted', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockResolvedValue({ data: { success: true } });

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/messages/m1/moderate', {
        action: 'delete',
      });
    });
    expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({ danger: true, confirmLabel: 'Delete' }),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Message deleted.');
  });

  it('does not call the API when the delete confirm is dismissed', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(false);

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(api.post).not.toHaveBeenCalled();
  });

  it('shows a toast error and keeps the row when the moderate call fails', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(new Error('server error'));

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Clear Flag' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Failed to clear the flag.');
    });
    // Row is still present -- the local removal only happens on the success path.
    expect(screen.getByText('Alice')).toBeTruthy();
  });

  it('confirms beacon abuse and reports the trust-score delta from the real response', async () => {
    const user = userEvent.setup();
    mockLoad({ beacons: { ...emptyBeacons, beacons: [beacon], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockResolvedValue({
      data: {
        success: true,
        removed: true,
        deployer_player_id: 'deployer-uuid',
        trust_before: 0.8,
        trust_after: 0.5,
        trust_dock: 0.3,
        aria_violation_count: 2,
      },
    });

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Bob')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Confirm Abuse' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/beacons/b1/confirm-abuse',
      );
    });
    expect(toastSuccess).toHaveBeenCalledWith(
      expect.stringContaining('0.80 → 0.50 (violation #2)'),
    );
  });

  it('paginates flagged messages using the page query param', async () => {
    const user = userEvent.setup();
    mockLoad({
      messages: {
        messages: [message],
        total: 40,
        page: 1,
        limit: 20,
        pages: 2,
      },
    });

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Page 1 of 2')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Next' }));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/messages/flagged?page=2');
    });
  });
});
