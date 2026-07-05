// @vitest-environment jsdom
/**
 * FirstLoginContext — resume behavior (WO-PUX-FLOGIN-RESUME).
 *
 * Pins the fix for the "reload mid-flow silently resets the session" defect
 * (the old :176-186 auto-DELETE-then-recreate block, now removed): a
 * resumed POST /session payload must hydrate dialogueHistory from the
 * persisted history, set currentPrompt to the last unanswered question, and
 * issue ZERO DELETE requests.
 *
 * apiClient and AuthContext are mocked at the module boundary (not a real
 * server), following the pattern in RoutePlannerPanel.test.tsx.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { mockGet, mockPost, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockDelete: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: mockGet, post: mockPost, delete: mockDelete },
}));

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1' }, isAuthenticated: true }),
}));

import { FirstLoginProvider, useFirstLogin } from '../FirstLoginContext';

const RESUMED_PAYLOAD = {
  session_id: 'sess-resume-1',
  player_id: 'player-1',
  available_ships: ['ESCAPE_POD', 'SCOUT_SHIP'],
  current_step: 'dialogue',
  npc_prompt: "What's your registration number?",
  exchange_id: 'exch-2',
  sequence_number: 2,
  ship_claimed: 'SCOUT_SHIP',
  outcome: null,
  resumed: true,
  dialogue_history: [
    {
      npc_prompt: 'Which vessel belongs to you?',
      player_response: 'The Scout Ship, obviously.',
      sequence_number: 1,
      persuasiveness: 0.6,
      confidence: 0.5,
      consistency: 0.8,
    },
    {
      npc_prompt: "What's your registration number?",
      player_response: '',
      sequence_number: 2,
      persuasiveness: null,
      confidence: null,
      consistency: null,
    },
  ],
  guard_name: 'Chen',
  guard_title: 'Security Officer',
  guard_trait: 'Friendly Veteran',
  guard_base_suspicion: 0.3,
  guard_description: "Experienced officer who's seen it all and can spot a good story",
};

let captured: ReturnType<typeof useFirstLogin> | null = null;

function Consumer() {
  captured = useFirstLogin();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('FirstLoginContext resume', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    captured = null;
    mockGet.mockReset();
    mockPost.mockReset();
    mockDelete.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('hydrates dialogueHistory and issues zero DELETE calls on a resumed session', async () => {
    mockGet.mockResolvedValue({
      data: { requires_first_login: true, session_id: 'sess-resume-1' },
    });
    mockPost.mockResolvedValue({ data: RESUMED_PAYLOAD });

    await act(async () => {
      root.render(
        <FirstLoginProvider>
          <Consumer />
        </FirstLoginProvider>
      );
    });
    // Flush the checkFirstLoginStatus -> startSession promise chain kicked
    // off by the provider's mount effect (fire-and-forget, not awaited by
    // the effect itself).
    await act(async () => {
      await flush();
      await flush();
    });

    expect(mockDelete).not.toHaveBeenCalled();
    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(mockPost).toHaveBeenCalledWith('/api/v1/first-login/session');

    expect(captured?.dialogueHistory).toHaveLength(2);
    expect(captured?.dialogueHistory[0]).toMatchObject({
      npc: 'Which vessel belongs to you?',
      player: 'The Scout Ship, obviously.',
    });
    expect(captured?.dialogueHistory[1]).toMatchObject({
      npc: "What's your registration number?",
      player: '',
    });
    // The current prompt is the last unanswered exchange, not the first.
    expect(captured?.currentPrompt).toBe("What's your registration number?");
    expect(captured?.exchangeId).toBe('exch-2');

    // Guard identity comes straight off the persisted session row.
    expect(captured?.session?.guard_name).toBe('Chen');
    expect(captured?.session?.guard_title).toBe('Security Officer');
    expect(captured?.session?.guard_base_suspicion).toBe(0.3);
    expect(captured?.session?.current_step).toBe('dialogue');
  });
});
