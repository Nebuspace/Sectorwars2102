// @vitest-environment jsdom
/**
 * LEG-3254 Soft-ORDER — OutcomeDisplay complete / recovery / TypeError densify.
 * invent=0: exercises existing complete + status helpers only.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const completeFirstLogin = vi.fn();
const checkFirstLoginStatus = vi.fn();
const onFirstLoginComplete = vi.fn();
const navigate = vi.fn();

const baseOutcome = {
  outcome: 'SUCCESS' as const,
  awarded_ship: 'SCOUT_SHIP',
  starting_credits: 1000,
  negotiation_skill: 3,
  negotiation_bonus: false,
  notoriety_penalty: false,
  extracted_player_name: null as string | null,
  guard_response: null as string | null,
  final_persuasion_score: 0.8,
};

const firstLoginState = {
  dialogueOutcome: baseOutcome,
  completeFirstLogin,
  checkFirstLoginStatus,
  isLoading: false,
};

vi.mock('../../../contexts/FirstLoginContext', () => {
  class FirstLoginAlreadyCompletedError extends Error {
    constructor(message = 'First login already completed') {
      super(message);
      this.name = 'FirstLoginAlreadyCompletedError';
    }
  }
  return {
    FirstLoginAlreadyCompletedError,
    useFirstLogin: () => firstLoginState,
    NicknameVerdict: undefined,
  };
});

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ onFirstLoginComplete }),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
}));

vi.mock('../NicknameConfirm', () => ({
  default: () => null,
}));

import OutcomeDisplay from '../OutcomeDisplay';
import { FirstLoginAlreadyCompletedError } from '../../../contexts/FirstLoginContext';

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe('OutcomeDisplay complete/recovery densify (LEG-3254)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    sessionStorage.clear();
    firstLoginState.isLoading = false;
    firstLoginState.dialogueOutcome = { ...baseOutcome, extracted_player_name: null };
    completeFirstLogin.mockResolvedValue({});
    checkFirstLoginStatus.mockResolvedValue(false);
    onFirstLoginComplete.mockResolvedValue(undefined);

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<OutcomeDisplay />);
    });
    await flush();
  };

  const clickBegin = async () => {
    const btn = container.querySelector('.outcome-start-button') as HTMLButtonElement;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn.click();
    });
    await flush();
  };

  it('happy-path complete arms onboarding and navigates to /game', async () => {
    await mount();
    await clickBegin();

    expect(completeFirstLogin).toHaveBeenCalled();
    expect(onFirstLoginComplete).toHaveBeenCalled();
    expect(sessionStorage.getItem('sw:onboarding:armed')).toBe('1');

    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    expect(navigate).toHaveBeenCalledWith('/game');
  });

  it('FirstLoginAlreadyCompletedError recovers when status recheck says done', async () => {
    completeFirstLogin.mockRejectedValue(new FirstLoginAlreadyCompletedError());
    checkFirstLoginStatus.mockResolvedValue(false);

    await mount();
    await clickBegin();

    expect(checkFirstLoginStatus).toHaveBeenCalled();
    expect(onFirstLoginComplete).toHaveBeenCalled();
    expect(container.textContent).toContain('Registration already completed -- resuming.');
    expect(sessionStorage.getItem('sw:onboarding:armed')).toBe('1');
    expect(container.querySelector('.error-message')).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    expect(navigate).toHaveBeenCalledWith('/game');
  });

  it('generic TypeError surfaces stable registration-failed copy without leak', async () => {
    completeFirstLogin.mockRejectedValue(new TypeError('Failed to fetch'));

    await mount();
    await clickBegin();

    const err = container.querySelector('.error-message');
    expect(err?.textContent).toBe('Failed to complete registration. Please try again.');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(navigate).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('sw:onboarding:armed')).toBeNull();
  });
});
