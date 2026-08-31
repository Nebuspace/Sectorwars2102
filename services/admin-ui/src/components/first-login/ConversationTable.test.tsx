import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationTable } from './ConversationTable';
import type { ConversationSummary } from '../../types/firstLogin';

const baseConversation: ConversationSummary = {
  session_id: 'session-1',
  player_username: 'testplayer',
  player_id: 'player-1',
  started_at: '2026-01-01T00:00:00Z',
  completed_at: '2026-01-01T00:10:00Z',
  ship_claimed: 'light_freighter',
  awarded_ship: 'light_freighter',
  outcome: 'SUCCESS',
  final_persuasion_score: 0.9,
  negotiation_skill: 'skilled',
  total_questions: 4,
  ai_providers_used: ['openai'],
  total_cost_usd: 0.02,
};

function conversation(overrides: Partial<ConversationSummary>): ConversationSummary {
  return { ...baseConversation, ...overrides };
}

describe('ConversationTable outcome and empty states (LEG-3127)', () => {
  it('shows loading state when loading with no rows', () => {
    render(
      <ConversationTable
        conversations={[]}
        onSelectConversation={vi.fn()}
        loading
      />,
    );

    expect(screen.getByText(/Loading conversations/i)).toBeTruthy();
  });

  it('shows empty state when not loading and no conversations', () => {
    render(
      <ConversationTable conversations={[]} onSelectConversation={vi.fn()} />,
    );

    expect(screen.getByText('No First Login Conversations Yet')).toBeTruthy();
    expect(
      screen.getByText(/Conversations will appear here when players complete/i),
    ).toBeTruthy();
  });

  it('renders outcome badges for known outcomes and unknown fallback', () => {
    const conversations = [
      conversation({ session_id: 's-success', outcome: 'SUCCESS' }),
      conversation({ session_id: 's-caught', outcome: 'CAUGHT' }),
      conversation({ session_id: 's-suspicious', outcome: 'SUSPICIOUS' }),
      conversation({ session_id: 's-abandoned', outcome: 'ABANDONED' }),
      conversation({ session_id: 's-null', outcome: null }),
      conversation({ session_id: 's-weird', outcome: 'MYSTERY' }),
    ];

    render(
      <ConversationTable
        conversations={conversations}
        onSelectConversation={vi.fn()}
      />,
    );

    expect(screen.getByText(/✅ SUCCESS/)).toBeTruthy();
    expect(screen.getByText(/🚫 CAUGHT/)).toBeTruthy();
    expect(screen.getByText(/⚠️ SUSPICIOUS/)).toBeTruthy();
    expect(screen.getByText(/🏃 ABANDONED/)).toBeTruthy();
    expect(screen.getByText(/❓ Unknown/)).toBeTruthy();
    expect(screen.getByText(/❓ MYSTERY/)).toBeTruthy();
  });

  it('shows in-progress badge and high-cost warning; selects conversation on view', () => {
    const onSelectConversation = vi.fn();

    render(
      <ConversationTable
        conversations={[
          conversation({
            session_id: 'in-progress',
            completed_at: null,
            total_cost_usd: 0.25,
          }),
        ]}
        onSelectConversation={onSelectConversation}
      />,
    );

    expect(screen.getByText(/⏳ In Progress/)).toBeTruthy();
    expect(screen.getByText(/\$0\.2500 ⚠️/)).toBeTruthy();
    expect(screen.getAllByText('Light Freighter').length).toBe(2);

    fireEvent.click(screen.getByTitle('View details'));
    expect(onSelectConversation).toHaveBeenCalledWith('in-progress');
  });
});
