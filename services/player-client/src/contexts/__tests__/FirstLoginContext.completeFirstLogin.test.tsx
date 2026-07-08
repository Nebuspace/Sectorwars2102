// @vitest-environment jsdom
/**
 * FirstLoginContext — completeFirstLogin nickname-verdict wiring
 * (WO-PUX-FLOGIN-NICKNAME). Confirms the POST body shape sent to
 * /first-login/complete for each verdict, and that a body-less call
 * (no verdict passed) matches the pre-existing decline-by-default
 * behavior.
 *
 * apiClient and AuthContext are mocked at the module boundary, following
 * FirstLoginContext.resume.test.tsx.
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

let captured: ReturnType<typeof useFirstLogin> | null = null;

function Consumer() {
  captured = useFirstLogin();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('FirstLoginContext completeFirstLogin', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    captured = null;
    mockGet.mockReset();
    mockPost.mockReset();
    mockDelete.mockReset();
    // No pending session for these tests -- keeps the mount-time
    // checkFirstLoginStatus effect from issuing extra POSTs.
    mockGet.mockResolvedValue({ data: { requires_first_login: false } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const mount = async () => {
    await act(async () => {
      root.render(
        <FirstLoginProvider>
          <Consumer />
        </FirstLoginProvider>
      );
    });
    // Drain the mount-time checkFirstLoginStatus promise chain (fire-and-
    // forget, not awaited by the effect itself) before issuing test calls.
    await act(async () => {
      await flush();
      await flush();
    });
  };

  it('a confirmed verdict sends nickname_confirmed:true and the extracted name via no override', async () => {
    await mount();
    mockPost.mockResolvedValueOnce({
      data: { player_id: 'p1', nickname: 'Zara', credits: 1000, ship: { id: 's1', name: 'x', type: 'ESCAPE_POD' }, negotiation_bonus: false, notoriety_penalty: false, nickname_rejected_reason: null },
    });

    await act(async () => {
      await captured!.completeFirstLogin({ confirmed: true, override: null });
    });

    expect(mockPost).toHaveBeenCalledWith('/api/v1/first-login/complete', {
      nickname_confirmed: true,
      nickname_override: null,
    });
  });

  it('a confirmed verdict with a retry override sends the override text', async () => {
    await mount();
    mockPost.mockResolvedValueOnce({
      data: { player_id: 'p1', nickname: 'ZaraV', credits: 1000, ship: { id: 's1', name: 'x', type: 'ESCAPE_POD' }, negotiation_bonus: false, notoriety_penalty: false, nickname_rejected_reason: null },
    });

    await act(async () => {
      await captured!.completeFirstLogin({ confirmed: true, override: 'ZaraV' });
    });

    expect(mockPost).toHaveBeenCalledWith('/api/v1/first-login/complete', {
      nickname_confirmed: true,
      nickname_override: 'ZaraV',
    });
  });

  it('a declined verdict sends nickname_confirmed:false', async () => {
    await mount();
    mockPost.mockResolvedValueOnce({
      data: { player_id: 'p1', nickname: null, credits: 1000, ship: { id: 's1', name: 'x', type: 'ESCAPE_POD' }, negotiation_bonus: false, notoriety_penalty: false, nickname_rejected_reason: null },
    });

    await act(async () => {
      await captured!.completeFirstLogin({ confirmed: false, override: null });
    });

    expect(mockPost).toHaveBeenCalledWith('/api/v1/first-login/complete', {
      nickname_confirmed: false,
      nickname_override: null,
    });
  });

  it('an omitted verdict (no extracted name) issues a body-less call, matching pre-existing behavior', async () => {
    await mount();
    mockPost.mockResolvedValueOnce({
      data: { player_id: 'p1', nickname: null, credits: 1000, ship: { id: 's1', name: 'x', type: 'ESCAPE_POD' }, negotiation_bonus: false, notoriety_penalty: false },
    });

    await act(async () => {
      await captured!.completeFirstLogin();
    });

    expect(mockPost).toHaveBeenCalledWith('/api/v1/first-login/complete', undefined);
  });

  it('surfaces nickname_rejected_reason on the result for the caller to display', async () => {
    await mount();
    mockPost.mockResolvedValueOnce({
      data: { player_id: 'p1', nickname: null, credits: 1000, ship: { id: 's1', name: 'x', type: 'ESCAPE_POD' }, negotiation_bonus: false, notoriety_penalty: false, nickname_rejected_reason: 'taken' },
    });

    let result: any;
    await act(async () => {
      result = await captured!.completeFirstLogin({ confirmed: true, override: 'Zara' });
    });

    expect(result.nickname_rejected_reason).toBe('taken');
  });
});
