// @vitest-environment jsdom
/**
 * LEG-3785 Soft-ORDER — FirstLoginContext typeErrorHonesty.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { mockGetStatus, mockStartSession, mockClaimShip } = vi.hoisted(() => ({
  mockGetStatus: vi.fn(),
  mockStartSession: vi.fn(),
  mockClaimShip: vi.fn(),
}));

vi.mock('../../services/apiClient', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>('../../services/api');
  return {
    ...actual,
    firstLoginAPI: {
      getStatus: (...a: unknown[]) => mockGetStatus(...a),
      startSession: (...a: unknown[]) => mockStartSession(...a),
      claimShip: (...a: unknown[]) => mockClaimShip(...a),
      submitDialogue: vi.fn(),
      complete: vi.fn(),
      resetSession: vi.fn(),
    },
  };
});

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1' }, isAuthenticated: true }),
}));

import {
  FirstLoginProvider,
  useFirstLogin,
  formatFirstLoginError,
} from '../FirstLoginContext';

let captured: ReturnType<typeof useFirstLogin> | null = null;
function Consumer() {
  captured = useFirstLogin();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('formatFirstLoginError TypeError densify (LEG-3785)', () => {
  it.each([
    ['TypeError', new TypeError('Failed to fetch'), 'Failed to check first login status.'],
    ['Network Error', new Error('Network Error'), 'Failed to start first login session.'],
    ['Failed to fetch', new Error('Failed to fetch'), 'Failed to claim ship. Please try again.'],
  ])('falls back on %s network collapse', (_label, err, fallback) => {
    const text = formatFirstLoginError(err, fallback);
    expect(text).toBe(fallback);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Network Error/i);
  });

  it('preserves server message for non-transport errors', () => {
    expect(
      formatFirstLoginError(new Error('Invalid ship selection.'), 'Failed to claim ship. Please try again.'),
    ).toBe('Invalid ship selection.');
  });
});

describe('FirstLoginContext transport collapse densify (LEG-3785)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    captured = null;
    mockGetStatus.mockReset();
    mockStartSession.mockReset();
    mockClaimShip.mockReset();
    mockGetStatus.mockResolvedValue({ requires_first_login: false });

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

  it.each([
  ['TypeError', new TypeError('Failed to fetch')],
  ['Network Error', new Error('Network Error')],
  ['Failed to fetch', new Error('Failed to fetch')],
])('checkFirstLoginStatus %s surfaces stable fallback without raw transport text', async (_label, err) => {
    mockGetStatus.mockRejectedValueOnce(err);
    await act(async () => {
      await captured!.checkFirstLoginStatus();
      await flush();
    });
    expect(captured!.error).toBe('Failed to check first login status.');
    expect(captured!.error).not.toMatch(/Failed to fetch/i);
    expect(captured!.error).not.toMatch(/TypeError/i);
    expect(captured!.error).not.toMatch(/Network Error/i);
  });

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('startSession %s surfaces stable fallback without raw transport text', async (_label, err) => {
    mockStartSession.mockRejectedValueOnce(err);
    await act(async () => {
      await captured!.startSession();
      await flush();
    });
    expect(captured!.error).toBe('Failed to start first login session.');
    expect(captured!.error).not.toMatch(/Failed to fetch/i);
    expect(captured!.error).not.toMatch(/TypeError/i);
    expect(captured!.error).not.toMatch(/Network Error/i);
  });

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('claimShip %s surfaces stable fallback without raw transport text', async (_label, err) => {
    mockClaimShip.mockRejectedValueOnce(err);
    await act(async () => {
      await captured!.claimShip('SCOUT', 'Hello guard.');
      await flush();
    });
    expect(captured!.error).toBe('Failed to claim ship. Please try again.');
    expect(captured!.error).not.toMatch(/Failed to fetch/i);
    expect(captured!.error).not.toMatch(/TypeError/i);
    expect(captured!.error).not.toMatch(/Network Error/i);
  });
});
