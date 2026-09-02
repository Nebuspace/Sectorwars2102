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
 * LEG-3668 Soft-ORDER — FirstLoginConversations TypeError/Network Error densify.
 * LEG-3906 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('FirstLoginConversations typeErrorHonesty densify (LEG-3668)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on conversations list load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByText(/An error occurred/i)).toBeTruthy();
    });
    const text = screen.getByText(/An error occurred/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on conversations list load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByText(/An error occurred/i)).toBeTruthy();
    });
    const text = screen.getByText(/An error occurred/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on conversation detail GET without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/conversations/session-1')) {
        return Promise.reject(new Error('Network Error'));
      }
      return Promise.resolve({ data: [sampleConversation] });
    });
    const user = userEvent.setup();

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'testplayer' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'testplayer' }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load conversation details/i)).toBeInTheDocument();
    });

    const text =
      screen.getByText(/Failed to load conversation details/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on conversation detail GET without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url.includes('/conversations/session-1')) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return Promise.resolve({ data: [sampleConversation] });
    });
    const user = userEvent.setup();

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'testplayer' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'testplayer' }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load conversation details/i)).toBeInTheDocument();
    });

    const text =
      screen.getByText(/Failed to load conversation details/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with first-login conversation scope copy when list GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByText(/Access denied|first-login conversation scopes/i)).toBeTruthy();
    });
    const text =
      screen.getByText(/Access denied|first-login conversation scopes/i).textContent ?? '';
    expect(text).toMatch(/Access denied|first-login conversation scopes/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on conversations list GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<FirstLoginConversations />);

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
