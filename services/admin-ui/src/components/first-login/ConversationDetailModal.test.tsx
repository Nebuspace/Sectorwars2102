import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationDetailModal } from './ConversationDetailModal';
import type { ConversationDetail } from '../../types/firstLogin';

function makeConversationDetail(): ConversationDetail {
  return {
    session: {
      session_id: 'sess-abc123',
      player_username: 'NewPilot',
      player_id: 'player-1',
      started_at: '2026-08-30T10:00:00Z',
      completed_at: '2026-08-30T10:15:00Z',
      ship_claimed: 'light_freighter',
      awarded_ship: 'light_freighter',
      outcome: 'success',
      final_persuasion_score: 0.82,
      negotiation_skill: 'intermediate',
      total_questions: 2,
      ai_providers_used: ['openai'],
      total_cost_usd: 0.045,
    },
    guard_personality: {
      name: 'Vex',
      title: 'Dock Marshal',
      trait: 'Skeptical',
      description: 'Questions every story twice.',
      base_suspicion: 0.35,
    },
    exchanges: [
      {
        id: 'ex-1',
        sequence_number: 1,
        npc_prompt: 'State your purpose at this dock.',
        player_response: 'Trading legitimate cargo.',
        timestamp: '2026-08-30T10:05:00Z',
        topic: 'purpose',
        persuasiveness: 0.75,
        confidence: 0.8,
        consistency: 0.7,
        believability: 0.72,
        current_suspicion: 0.4,
        detected_contradictions: null,
        ai_provider: 'openai',
        response_time_ms: 420,
        estimated_cost_usd: 0.02,
        tokens_used: 180,
      },
    ],
  };
}

describe('ConversationDetailModal export/guard (LEG-3131)', () => {
  it('returns null when conversation is null', () => {
    const { container } = render(
      <ConversationDetailModal conversation={null} onClose={() => {}} />,
    );

    expect(container.firstChild).toBeNull();
  });

  it('invokes onExport when Export is clicked', () => {
    const conversation = makeConversationDetail();
    const onExport = vi.fn();
    const onClose = vi.fn();

    render(
      <ConversationDetailModal
        conversation={conversation}
        onClose={onClose}
        onExport={onExport}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Export/i }));

    expect(onExport).toHaveBeenCalledTimes(1);
    expect(onExport).toHaveBeenCalledWith(conversation);
  });

  describe('default JSON export', () => {
    let createObjectURL: ReturnType<typeof vi.fn>;
    let revokeObjectURL: ReturnType<typeof vi.fn>;
    let clickSpy: ReturnType<typeof vi.fn>;
    let appendChildSpy: ReturnType<typeof vi.spyOn>;
    let removeChildSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      createObjectURL = vi.fn(() => 'blob:mock-url');
      revokeObjectURL = vi.fn();
      clickSpy = vi.fn();

      vi.stubGlobal('URL', {
        createObjectURL: createObjectURL,
        revokeObjectURL: revokeObjectURL,
      });

      const originalCreateElement = document.createElement.bind(document);
      vi.spyOn(document, 'createElement').mockImplementation((tagName: string) => {
        const el = originalCreateElement(tagName);
        if (tagName === 'a') {
          el.click = clickSpy;
        }
        return el;
      });

      appendChildSpy = vi.spyOn(document.body, 'appendChild');
      removeChildSpy = vi.spyOn(document.body, 'removeChild');
    });

    afterEach(() => {
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    });

    it('creates a download link when onExport is not provided', () => {
      const conversation = makeConversationDetail();

      render(
        <ConversationDetailModal conversation={conversation} onClose={() => {}} />,
      );

      fireEvent.click(screen.getByRole('button', { name: /Export/i }));

      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(clickSpy).toHaveBeenCalledTimes(1);
      expect(appendChildSpy).toHaveBeenCalled();
      expect(removeChildSpy).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
    });
  });

  it('toggles prompt visibility when View AI Prompts is clicked', () => {
    const conversation = makeConversationDetail();

    render(
      <ConversationDetailModal conversation={conversation} onClose={() => {}} />,
    );

    expect(document.querySelector('.prompt-content')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /View AI Prompts/i }));

    expect(document.querySelector('.prompt-content')).toBeTruthy();
    expect(document.querySelector('.prompt-text')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /View AI Prompts/i }));

    expect(document.querySelector('.prompt-content')).toBeNull();
  });
});
