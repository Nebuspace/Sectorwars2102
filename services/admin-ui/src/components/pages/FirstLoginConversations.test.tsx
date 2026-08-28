import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FirstLoginConversations from './FirstLoginConversations';
import { api } from '../../utils/auth';
import type { ConversationSummary } from '../../types/firstLogin';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../first-login/ConversationFilters', () => ({
  ConversationFilters: () => null,
}));

vi.mock('../first-login/ConversationTable', () => ({
  ConversationTable: ({
    conversations,
    onSelectConversation,
  }: {
    conversations: ConversationSummary[];
    onSelectConversation: (sessionId: string) => void;
  }) => (
    <div data-testid="conversation-table">
      {conversations.map((conversation) => (
        <button
          key={conversation.session_id}
          type="button"
          onClick={() => onSelectConversation(conversation.session_id)}
        >
          {conversation.player_username}
        </button>
      ))}
    </div>
  ),
}));

vi.mock('../first-login/ConversationDetailModal', () => ({
  ConversationDetailModal: () => null,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

describe('FirstLoginConversations scope errors (LEG-967)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.first_login.view'),
    );

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.first_login\.view/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

const sampleConversation: ConversationSummary = {
  session_id: 'session-1',
  player_username: 'testplayer',
  player_id: 'player-1',
  started_at: '2026-01-01T00:00:00Z',
  completed_at: '2026-01-01T00:05:00Z',
  ship_claimed: 'Scout',
  awarded_ship: 'Scout',
  outcome: 'success',
  final_persuasion_score: 0.8,
  negotiation_skill: 'good',
  total_questions: 4,
  ai_providers_used: ['openai'],
  total_cost_usd: 0.0123,
};

describe('FirstLoginConversations detail GET formatAdminApiError (LEG-2680)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/conversations/session-1')) {
        return Promise.resolve({ data: { session: { session_id: 'session-1' } } });
      }
      return Promise.resolve({ data: [sampleConversation] });
    });
  });

  async function selectConversation(user: ReturnType<typeof userEvent.setup>) {
    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'testplayer' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'testplayer' }));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        '/api/v1/admin/first-login/conversations/session-1',
      );
    });
  }

  it('surfaces scope detail on conversation detail GET 403', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/conversations/session-1')) {
        return Promise.reject(
          axiosError(403, 'Missing scope admin.first_login.view'),
        );
      }
      return Promise.resolve({ data: [sampleConversation] });
    });
    const user = userEvent.setup();
    await selectConversation(user);

    await waitFor(() => {
      expect(
        screen.getByText(/Missing scope admin\.first_login\.view/i),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByText('Failed to load conversation details'),
    ).not.toBeInTheDocument();
  });

  it('surfaces rate-limit copy on conversation detail GET 429', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/conversations/session-1')) {
        return Promise.reject(axiosError(429));
      }
      return Promise.resolve({ data: [sampleConversation] });
    });
    const user = userEvent.setup();
    await selectConversation(user);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByText('Failed to load conversation details'),
    ).not.toBeInTheDocument();
  });
});
