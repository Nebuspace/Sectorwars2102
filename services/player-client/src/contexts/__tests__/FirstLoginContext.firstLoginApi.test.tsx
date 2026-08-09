// @vitest-environment jsdom
/**
 * FirstLoginContext — firstLoginAPI wrappers (WO-WIRE-FIRST-LOGIN-CONTEXT-API).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const {
  mockGet,
  mockPost,
  mockDelete,
  mockGetStatus,
  mockStartSession,
  mockClaimShip,
  mockSubmitDialogue,
  mockComplete,
  mockResetSession,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockDelete: vi.fn(),
  mockGetStatus: vi.fn(),
  mockStartSession: vi.fn(),
  mockClaimShip: vi.fn(),
  mockSubmitDialogue: vi.fn(),
  mockComplete: vi.fn(),
  mockResetSession: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: mockGet, post: mockPost, delete: mockDelete },
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>('../../services/api');
  return {
    ...actual,
    firstLoginAPI: {
      getStatus: (...a: unknown[]) => mockGetStatus(...a),
      startSession: (...a: unknown[]) => mockStartSession(...a),
      claimShip: (...a: unknown[]) => mockClaimShip(...a),
      submitDialogue: (...a: unknown[]) => mockSubmitDialogue(...a),
      complete: (...a: unknown[]) => mockComplete(...a),
      resetSession: (...a: unknown[]) => mockResetSession(...a),
    },
  };
});

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1' }, isAuthenticated: true }),
}));

import { FirstLoginProvider, useFirstLogin } from '../FirstLoginContext';

let captured: ReturnType<typeof useFirstLogin> | null = null;
function Consumer() {
  captured = useFirstLogin();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('FirstLoginContext firstLoginAPI wire', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    captured = null;
    mockGet.mockResolvedValue({ data: {} });
    mockPost.mockResolvedValue({ data: {} });
    mockDelete.mockResolvedValue({ data: {} });
    mockGetStatus.mockResolvedValue({ requires_first_login: false });
    mockStartSession.mockResolvedValue({
      session_id: 's1',
      player_id: 'p1',
      available_ships: ['SCOUT'],
      current_step: 'ship_selection',
      npc_prompt: 'Hello',
    });
    mockClaimShip.mockResolvedValue({
      session_id: 's1',
      current_step: 'dialogue',
      npc_prompt: 'Why?',
      exchange_id: 'ex-1',
    });
    mockSubmitDialogue.mockResolvedValue({
      exchange_id: 'ex-1',
      analysis: { persuasiveness: 1, confidence: 1, consistency: 1 },
      is_final: false,
      next_question: 'More?',
      next_exchange_id: 'ex-2',
    });
    mockComplete.mockResolvedValue({
      player_id: 'p1',
      credits: 1000,
      ship: { id: 'ship-1', name: 'x', type: 'SCOUT' },
      negotiation_bonus: false,
      notoriety_penalty: false,
    });
    mockResetSession.mockResolvedValue(undefined);

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <FirstLoginProvider>
          <Consumer />
        </FirstLoginProvider>,
      );
      await flush();
      await flush();
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it('routes session helpers through firstLoginAPI (no raw /first-login traffic)', async () => {
    mockGet.mockClear();
    mockPost.mockClear();
    mockDelete.mockClear();
    mockGetStatus.mockClear();

    await act(async () => {
      await captured!.checkFirstLoginStatus();
      await captured!.startSession();
      await captured!.claimShip('SCOUT', 'I need a ship');
      // claimShip sets exchangeId from response
      await flush();
    });

    // Remount path may have already called getStatus; assert wrappers after clear.
    expect(mockGetStatus).toHaveBeenCalled();
    expect(mockStartSession).toHaveBeenCalled();
    expect(mockClaimShip).toHaveBeenCalledWith({
      ship_type: 'SCOUT',
      dialogue_response: 'I need a ship',
    });

    await act(async () => {
      // exchangeId from claimShip
      if (captured!.exchangeId) {
        await captured!.submitResponse('Because');
      }
      await captured!.completeFirstLogin({ confirmed: false, override: null });
      await captured!.resetSession();
      await flush();
    });

    expect(mockComplete).toHaveBeenCalledWith({
      nickname_confirmed: false,
      nickname_override: null,
    });
    expect(mockResetSession).toHaveBeenCalled();

    const raw = [...mockGet.mock.calls, ...mockPost.mock.calls, ...mockDelete.mock.calls].filter(
      (c) => String(c[0]).includes('/first-login'),
    );
    expect(raw).toHaveLength(0);
  });
});
