import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import MessageModeration, {
  buildEscalationAuditLedgerHref,
} from './MessageModeration';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
const toastInfo = vi.fn();
const toastWarning = vi.fn();
const confirmMock = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    info: toastInfo,
    warning: toastWarning,
  }),
  useConfirm: () => confirmMock,
}));

const subscribeMock = vi.fn();
let isConnectedMock = true;
let flaggedAlertHandler: ((data: any) => void) | null = null;

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    isConnected: isConnectedMock,
    subscribe: subscribeMock,
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

function renderPage() {
  return render(
    <MemoryRouter>
      <MessageModeration />
    </MemoryRouter>,
  );
}

describe('MessageModeration', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    toastInfo.mockReset();
    toastWarning.mockReset();
    confirmMock.mockReset();
    subscribeMock.mockReset();
    flaggedAlertHandler = null;
    isConnectedMock = true;
    subscribeMock.mockImplementation((event: string, handler: (data: any) => void) => {
      if (event === 'flagged:message:alert') {
        flaggedAlertHandler = handler;
      }
      return () => {
        if (flaggedAlertHandler === handler) flaggedAlertHandler = null;
      };
    });
  });

  it('shows honest empty states when nothing is flagged', async () => {
    mockLoad();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('No flagged messages.')).toBeTruthy();
    });
    expect(screen.getByText('No flagged sector beacons.')).toBeTruthy();
  });

  it('shows escalation block-count badge when sender_block_count_30d is 2 (LEG-2690)', async () => {
    mockLoad({
      messages: {
        ...emptyMessages,
        messages: [{ ...message, sender_block_count_30d: 2 }],
        total: 1,
      },
    });

    render(<MessageModeration />);

    await waitFor(() => {
      expect(screen.getByText('2 blocks/30d')).toBeTruthy();
    });
    expect(screen.getByLabelText(/Sender escalation risk/i)).toBeTruthy();
    expect(screen.queryByText('1 block/30d')).toBeNull();
  });

  it('omits block-count badge when sender_block_count_30d is 0 (LEG-2690)', async () => {
    mockLoad({
      messages: {
        ...emptyMessages,
        messages: [{ ...message, sender_block_count_30d: 0 }],
        total: 1,
      },
    });

    render(<MessageModeration />);

    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());
    expect(screen.queryByText(/block\/30d/i)).toBeNull();
    expect(screen.queryByLabelText(/Sender block history/i)).toBeNull();
    expect(screen.queryByLabelText(/Sender escalation risk/i)).toBeNull();
  });

  it('omits block-count badge when sender_block_count_30d is absent (LEG-2690 rollout)', async () => {
    mockLoad({
      messages: { ...emptyMessages, messages: [message], total: 1 },
    });

    render(<MessageModeration />);

    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());
    expect(screen.queryByText(/block\/30d/i)).toBeNull();
  });

  it('renders flagged messages and beacons from the real endpoints', async () => {
    mockLoad({
      messages: { ...emptyMessages, messages: [message], total: 1 },
      beacons: { ...emptyBeacons, beacons: [beacon], total: 1 },
      stats: { ...emptyStats, total_messages: 10, flagged_messages: 1 },
    });

    renderPage();

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

    renderPage();

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

    renderPage();

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

    renderPage();
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

  it('accept posts tip canon moderation path (LEG-1579)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockResolvedValue({
      data: {
        success: true,
        action: 'accept',
        message_id: 'm1',
        rep_delta: 0,
        sender_notified: false,
        block_count_30d: 0,
        escalation_audit_logged: false,
      },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Accept' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/accept',
        {},
      );
    });
    expect(toastSuccess).toHaveBeenCalledWith('Flag accepted.');
  });

  it('surfaces formatAdminApiError on accept POST 403 (LEG-2660)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.security.act' },
        },
      }),
    );

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Accept' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/accept',
        {},
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/admin\.security\.act|Missing scope|Access denied/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to accept the message');
  });

  it('surfaces rate-limit copy on accept POST 429 (LEG-2660)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429 },
      }),
    );

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Accept' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/accept',
        {},
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to accept the message');
  });

  it('redact posts tip canon path and surfaces reputation delta (LEG-1579)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockResolvedValue({
      data: {
        success: true,
        action: 'redact',
        message_id: 'm1',
        rep_delta: -50,
        sender_notified: true,
        block_count_30d: 0,
        escalation_audit_logged: false,
      },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Redact' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/redact',
        {},
      );
    });
    expect(toastSuccess).toHaveBeenCalledWith(
      expect.stringMatching(/Message redacted\..*Reputation Δ -50/),
    );
  });

  it('surfaces formatAdminApiError on redact POST 403 (LEG-2645)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.security.act' },
        },
      }),
    );

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Redact' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/redact',
        {},
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/admin\.security\.act|Missing scope|Access denied/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to redact the message');
  });

  it('surfaces rate-limit copy on redact POST 429 (LEG-2645)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429 },
      }),
    );

    render(<MessageModeration />);
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Redact' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/redact',
        {},
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to redact the message');
  });

  it('block posts tip canon path and surfaces formatAdminApiError on 403 (LEG-1579)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: { detail: 'Missing scope admin.security.act' } },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Block' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/block',
        {},
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/admin\.security\.act|Missing scope|Access denied/i),
    );
  });

  it('surfaces rate-limit copy on block POST 429 (LEG-2665)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429, data: { detail: 'Too Many Requests' } },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Block' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/moderation/messages/m1/block',
        {},
      );
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Failed to block the message');
  });

  it('block escalation surfaces audit ledger deep link when flagged (LEG-2703)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockResolvedValue({
      data: {
        success: true,
        action: 'block',
        message_id: 'm1',
        rep_delta: -100,
        sender_notified: true,
        block_count_30d: 2,
        escalation_audit_logged: true,
      },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Block' }));

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        expect.stringMatching(/Message blocked\..*Escalation audit logged/),
      );
    });

    const auditLink = screen.getByRole('link', { name: 'View audit ledger entry' });
    expect(auditLink).toHaveAttribute(
      'href',
      buildEscalationAuditLedgerHref('sender-uuid'),
    );
  });

  it('buildEscalationAuditLedgerHref encodes sender filter query (LEG-2703)', () => {
    expect(buildEscalationAuditLedgerHref('sender-uuid')).toBe(
      '/audit?tab=ledger&target_type=player&target_id=sender-uuid',
    );
  });

  it('does not call the API when the delete confirm is dismissed', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(false);

    renderPage();
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

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Clear Flag' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        expect.stringContaining('Failed to clear the flag'),
      );
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

    renderPage();
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

  it('surfaces formatAdminApiError on beacon clear-flag POST 403 (LEG-2648)', async () => {
    const user = userEvent.setup();
    mockLoad({ beacons: { ...emptyBeacons, beacons: [beacon], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.beacons.moderate' },
        },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Bob')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Clear Flag' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/beacons/b1/clear-flag',
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.beacons.moderate');
    expect(toastError).not.toHaveBeenCalledWith('Failed to clear the beacon flag');
  });

  it('surfaces rate-limit copy on beacon clear-flag POST 429 (LEG-2648)', async () => {
    const user = userEvent.setup();
    mockLoad({ beacons: { ...emptyBeacons, beacons: [beacon], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429 },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Bob')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Clear Flag' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/beacons/b1/clear-flag',
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to clear the beacon flag');
  });

  it('surfaces formatAdminApiError on confirm-abuse POST 403 (LEG-2649)', async () => {
    const user = userEvent.setup();
    mockLoad({ beacons: { ...emptyBeacons, beacons: [beacon], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: { status: 403, data: {} },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Bob')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Confirm Abuse' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/beacons/b1/confirm-abuse',
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/admin\.beacons\.moderate|Access denied/i),
    );
    expect(toastError).not.toHaveBeenCalledWith(
      'Failed to confirm abuse for this beacon',
    );
  });

  it('surfaces rate-limit copy on confirm-abuse POST 429 (LEG-2649)', async () => {
    const user = userEvent.setup();
    mockLoad({ beacons: { ...emptyBeacons, beacons: [beacon], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429 },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Bob')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Confirm Abuse' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/beacons/b1/confirm-abuse',
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith(
      'Failed to confirm abuse for this beacon',
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

    renderPage();
    await waitFor(() => expect(screen.getByText('Page 1 of 2')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Next' }));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/messages/flagged?page=2');
    });
  });

  it('subscribes to flagged:message:alert and debounced-refetches on alert', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockLoad();
    renderPage();

    await waitFor(() => {
      expect(subscribeMock).toHaveBeenCalledWith(
        'flagged:message:alert',
        expect.any(Function),
      );
    });
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/admin/messages/flagged?page=1');
    });

    const getsAfterMount = vi.mocked(api.get).mock.calls.length;
    expect(flaggedAlertHandler).toBeTruthy();

    flaggedAlertHandler!({
      type: 'flagged_message_alert',
      flagged_by_name: 'Carol',
      reason: 'harassment',
      message_preview: 'bad text',
    });

    expect(toastInfo).toHaveBeenCalledWith(
      expect.stringContaining('Carol: harassment'),
    );

    // Debounce window — no immediate second fetch stampede.
    expect(vi.mocked(api.get).mock.calls.length).toBe(getsAfterMount);

    await vi.advanceTimersByTimeAsync(450);

    await waitFor(() => {
      expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(getsAfterMount);
    });
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/messages/flagged?page=1');
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/messages/stats');

    vi.useRealTimers();
  });

  it('does not register handlers for unrelated WS event types', async () => {
    mockLoad();
    renderPage();

    await waitFor(() => expect(subscribeMock).toHaveBeenCalled());
    const events = subscribeMock.mock.calls.map((c) => c[0]);
    expect(events).toEqual(['flagged:message:alert']);
    expect(events).not.toContain('ai:model-update');
    expect(events).not.toContain('system:alert');
  });

  it('shows honest live-update demotion when WebSocket is disconnected', async () => {
    isConnectedMock = false;
    mockLoad();
    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText('Live updates unavailable — use Refresh'),
      ).toBeTruthy();
    });
    expect(screen.getAllByRole('button', { name: 'Refresh' }).length).toBeGreaterThan(0);
  });

  it('surfaces scope denial on 403 flagged-message load (LEG-967)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 403'), {
            response: {
              status: 403,
              data: { detail: 'Missing scope admin.messages.moderate' },
            },
          }),
        );
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.messages\.moderate/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 flagged-message load (LEG-2939)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 429'), {
            response: { status: 429 },
          }),
        );
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
    expect(
      screen.queryByText('Failed to load the flagged-message review queue.'),
    ).toBeNull();
    expect(screen.queryByText(/HTTP 429/i)).toBeNull();
  });

  it('shows rate-limit copy on 429 stats load (LEG-967)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.reject(
          Object.assign(new Error('HTTP 429'), {
            response: { status: 429 },
          }),
        );
      }
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.resolve({ data: emptyMessages });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces scope denial on 403 flagged-beacon load (LEG-2719)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/beacons/flagged')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 403'), {
            response: {
              status: 403,
              data: { detail: 'Missing scope admin.beacons.moderate' },
            },
          }),
        );
      }
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.resolve({ data: emptyMessages });
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    render(<MessageModeration />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.beacons\.moderate/i)).toBeTruthy();
    });
    expect(screen.queryByText('No flagged sector beacons.')).toBeNull();
  });

  it('shows rate-limit copy on 429 flagged-beacon load (LEG-2719)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/admin/beacons/flagged')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 429'), {
            response: { status: 429 },
          }),
        );
      }
      if (url.startsWith('/api/v1/admin/messages/flagged')) {
        return Promise.resolve({ data: emptyMessages });
      }
      if (url === '/api/v1/admin/messages/stats') {
        return Promise.resolve({ data: emptyStats });
      }
      return Promise.resolve({ data: emptyBeacons });
    });

    render(<MessageModeration />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    expect(screen.queryByText('No flagged sector beacons.')).toBeNull();
  });

  it('select-all toggles every message on the current page', async () => {
    const user = userEvent.setup();
    const message2 = { ...message, id: 'm2', sender_name: 'Dana' };
    mockLoad({
      messages: {
        ...emptyMessages,
        messages: [message, message2],
        total: 2,
      },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(
      screen.getByRole('checkbox', { name: 'Select all messages on this page' }),
    );
    expect(screen.getByTestId('bulk-action-bar')).toBeTruthy();
    expect(screen.getByText('2 selected')).toBeTruthy();

    await user.click(
      screen.getByRole('checkbox', { name: 'Select all messages on this page' }),
    );
    expect(screen.queryByTestId('bulk-action-bar')).toBeNull();
  });

  it('bulk delete posts selected ids to bulk-moderate and reports partial failures', async () => {
    const user = userEvent.setup();
    const message2 = { ...message, id: 'm2', sender_name: 'Dana' };
    mockLoad({
      messages: {
        ...emptyMessages,
        messages: [message, message2],
        total: 2,
      },
    });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockResolvedValue({
      data: {
        action: 'delete',
        succeeded: 1,
        failed: 1,
        results: [
          { message_id: 'm1', success: true, detail: null },
          { message_id: 'm2', success: false, detail: 'not found' },
        ],
      },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('checkbox', { name: 'Select message m1' }));
    await user.click(screen.getByRole('checkbox', { name: 'Select message m2' }));
    await user.click(screen.getByRole('button', { name: 'Bulk Delete' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/messages/bulk-moderate',
        { message_ids: ['m1', 'm2'], action: 'delete' },
      );
    });
    expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({ danger: true, confirmLabel: 'Delete selected' }),
    );
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastWarning).toHaveBeenCalledWith(
      'Bulk delete: 1 succeeded, 1 failed.',
    );
    await waitFor(() => {
      expect(screen.getByTestId('bulk-partial-failures')).toBeTruthy();
    });
    expect(screen.getByText(/m2: not found/)).toBeTruthy();
  });

  it('bulk clear-flag posts correct payload when all selections succeed', async () => {
    const user = userEvent.setup();
    const message2 = { ...message, id: 'm2', sender_name: 'Dana' };
    mockLoad({
      messages: {
        ...emptyMessages,
        messages: [message, message2],
        total: 2,
      },
    });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockResolvedValue({
      data: {
        action: 'unflag',
        succeeded: 2,
        failed: 0,
        results: [
          { message_id: 'm1', success: true },
          { message_id: 'm2', success: true },
        ],
      },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(
      screen.getByRole('checkbox', { name: 'Select all messages on this page' }),
    );
    await user.click(screen.getByRole('button', { name: 'Bulk Clear Flag' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/messages/bulk-moderate',
        { message_ids: ['m1', 'm2'], action: 'unflag' },
      );
    });
    expect(toastWarning).not.toHaveBeenCalled();
    expect(toastSuccess).toHaveBeenCalledWith('Cleared flags on 2 messages.');
  });

  it('surfaces formatAdminApiError on bulk delete 403 (LEG-2442)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.messages.moderate' },
        },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('checkbox', { name: 'Select message m1' }));
    await user.click(screen.getByRole('button', { name: 'Bulk Delete' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/messages/bulk-moderate',
        { message_ids: ['m1'], action: 'delete' },
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      'Missing scope admin.messages.moderate',
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to bulk-delete messages.');
  });

  it('surfaces rate-limit copy on bulk delete 429 (LEG-2442)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429 },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('checkbox', { name: 'Select message m1' }));
    await user.click(screen.getByRole('button', { name: 'Bulk Delete' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/messages/bulk-moderate',
        { message_ids: ['m1'], action: 'delete' },
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to bulk-delete messages.');
  });

  it('surfaces formatAdminApiError on bulk clear-flag 403 (LEG-2442)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.messages.moderate' },
        },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('checkbox', { name: 'Select message m1' }));
    await user.click(screen.getByRole('button', { name: 'Bulk Clear Flag' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/messages/bulk-moderate',
        { message_ids: ['m1'], action: 'unflag' },
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      'Missing scope admin.messages.moderate',
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to bulk-clear flags.');
  });

  it('surfaces rate-limit copy on bulk clear-flag 429 (LEG-2442)', async () => {
    const user = userEvent.setup();
    mockLoad({ messages: { ...emptyMessages, messages: [message], total: 1 } });
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429 },
      }),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText('Alice')).toBeTruthy());

    await user.click(screen.getByRole('checkbox', { name: 'Select message m1' }));
    await user.click(screen.getByRole('button', { name: 'Bulk Clear Flag' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/messages/bulk-moderate',
        { message_ids: ['m1'], action: 'unflag' },
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Failed to bulk-clear flags.');
  });
});
