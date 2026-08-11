// @vitest-environment jsdom
/**
 * useFirstSession — WO-PUX-ONBOARD first-session orientation tracker
 * (dock / trade / travel objectives, arm/retire/dismiss lifecycle).
 * Follows the useAnnunciatorState.test.tsx harness convention: a host
 * component captures the hook's return into a module-level slot every
 * render. useGame/useWebSocket are mocked; sessionStorage/localStorage
 * are real (jsdom) and cleared before each test, since the hook's own
 * persistence IS the behavior under test.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { useGameMock, useWebSocketMock } = vi.hoisted(() => ({
  useGameMock: vi.fn(),
  useWebSocketMock: vi.fn(),
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: useGameMock,
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: useWebSocketMock,
}));

import useFirstSession, { type UseFirstSessionResult } from '../useFirstSession';

const SESSION_ARM_KEY = 'sw:onboarding:armed';
const retiredKey = (id: string) => `sw:onboarding:retired:${id}`;
const progressKey = (id: string) => `sw:onboarding:progress:${id}`;

let latest: UseFirstSessionResult | null = null;

function Harness() {
  latest = useFirstSession();
  return null;
}

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

const player = (over: Partial<{ id: string; is_docked: boolean; current_sector_id: number }> = {}) => ({
  id: 'player-1',
  is_docked: false,
  current_sector_id: 1,
  ...over,
});

const setGame = (playerState: ReturnType<typeof player> | null) => {
  useGameMock.mockReturnValue({ playerState });
};

const setNotifications = (notifications: Array<{ title: string }>) => {
  useWebSocketMock.mockReturnValue({ notifications });
};

const render = async () => {
  await act(async () => {
    root.render(<Harness />);
  });
};

const arm = () => sessionStorage.setItem(SESSION_ARM_KEY, '1');

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  latest = null;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  setNotifications([]);
  setGame(player());
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
});

describe('useFirstSession — arm/retire visibility', () => {
  it('is invisible with an empty progress set when never armed this session', async () => {
    await render();
    expect(latest!.visible).toBe(false);
    expect(latest!.progress).toEqual({ dock: false, trade: false, travel: false });
  });

  it('becomes visible once armed via sessionStorage and not retired', async () => {
    arm();
    await render();
    expect(latest!.visible).toBe(true);
  });

  it('stays invisible when armed but already retired for this player', async () => {
    arm();
    localStorage.setItem(retiredKey('player-1'), '1');
    await render();
    expect(latest!.visible).toBe(false);
  });

  it('stays invisible with no playerState at all, regardless of arming', async () => {
    arm();
    setGame(null);
    await render();
    expect(latest!.visible).toBe(false);
  });

  it('hydrates prior progress from localStorage on init', async () => {
    arm();
    localStorage.setItem(progressKey('player-1'), JSON.stringify({ dock: true, trade: false, travel: false }));
    await render();
    expect(latest!.progress).toEqual({ dock: true, trade: false, travel: false });
  });

  it('falls back to empty progress on corrupted stored JSON', async () => {
    arm();
    localStorage.setItem(progressKey('player-1'), '{not-json');
    await render();
    expect(latest!.progress).toEqual({ dock: false, trade: false, travel: false });
  });
});

describe('useFirstSession — dock objective', () => {
  it('ticks dock when the player is docked while armed and not retired', async () => {
    arm();
    await render();
    expect(latest!.progress.dock).toBe(false);

    setGame(player({ is_docked: true }));
    await render();
    expect(latest!.progress.dock).toBe(true);
    expect(JSON.parse(localStorage.getItem(progressKey('player-1'))!)).toMatchObject({ dock: true });
  });

  it('never ticks dock when not armed this session', async () => {
    setGame(player({ is_docked: true }));
    await render();
    expect(latest!.progress.dock).toBe(false);
  });

  it('never re-ticks (idempotent) once dock is already true', async () => {
    arm();
    setGame(player({ is_docked: true }));
    await render();
    expect(latest!.progress.dock).toBe(true);
    localStorage.removeItem(progressKey('player-1')); // if it re-wrote, this would repopulate on next tick attempt
    await render(); // another render with is_docked still true
    expect(localStorage.getItem(progressKey('player-1'))).toBeNull();
  });
});

describe('useFirstSession — travel objective', () => {
  it('ticks travel once the current sector diverges from the armed-session baseline', async () => {
    arm();
    setGame(player({ current_sector_id: 5 }));
    await render(); // baseline captured at sector 5
    expect(latest!.progress.travel).toBe(false);

    setGame(player({ current_sector_id: 6 }));
    await render();
    expect(latest!.progress.travel).toBe(true);
  });

  it('never ticks travel while the sector stays at the baseline', async () => {
    arm();
    setGame(player({ current_sector_id: 5 }));
    await render();
    await render(); // re-render, same sector
    expect(latest!.progress.travel).toBe(false);
  });
});

describe('useFirstSession — trade objective', () => {
  it('ticks trade when the newest notification is a Trade Successful entry', async () => {
    arm();
    await render();
    expect(latest!.progress.trade).toBe(false);

    setNotifications([{ title: 'Trade Successful' }]);
    await render();
    expect(latest!.progress.trade).toBe(true);
  });

  it('ignores notifications with a different title', async () => {
    arm();
    setNotifications([{ title: 'Combat Started' }]);
    await render();
    expect(latest!.progress.trade).toBe(false);
  });

  it('only inspects the newest (index 0) notification', async () => {
    arm();
    setNotifications([{ title: 'Combat Started' }, { title: 'Trade Successful' }]);
    await render();
    expect(latest!.progress.trade).toBe(false);
  });
});

describe('useFirstSession — completion + retirement', () => {
  it('auto-retires (and goes invisible) the instant all three objectives complete', async () => {
    arm();
    localStorage.setItem(
      progressKey('player-1'),
      JSON.stringify({ dock: true, trade: true, travel: false })
    );
    setGame(player({ current_sector_id: 5 }));
    await render(); // baseline captured, dock/trade already true from storage
    expect(latest!.visible).toBe(true);
    expect(latest!.allComplete).toBe(false);

    setGame(player({ current_sector_id: 6 })); // ticks travel -> allComplete
    await render();
    expect(latest!.allComplete).toBe(true);
    expect(latest!.visible).toBe(false); // auto-retired
    expect(localStorage.getItem(retiredKey('player-1'))).toBe('1');
  });

  it('dismiss() permanently retires regardless of completion state', async () => {
    arm();
    await render();
    expect(latest!.visible).toBe(true);

    await act(async () => {
      latest!.dismiss();
    });
    expect(latest!.visible).toBe(false);
    expect(localStorage.getItem(retiredKey('player-1'))).toBe('1');
  });

  it('a dismissed/retired player never re-shows on a later remount in the same session', async () => {
    arm();
    await render();
    await act(async () => {
      latest!.dismiss();
    });

    // Fresh mount (new component instance), same armed session.
    await act(async () => {
      root.unmount();
    });
    root = createRoot(container);
    await render();
    expect(latest!.visible).toBe(false);
  });
});

describe('useFirstSession — per-player isolation', () => {
  it('re-initializes armed/retired/progress state when the active player changes', async () => {
    arm();
    localStorage.setItem(retiredKey('player-1'), '1');
    await render();
    expect(latest!.visible).toBe(false);

    setGame(player({ id: 'player-2' }));
    await render();
    expect(latest!.visible).toBe(true); // player-2 has no retired marker
    expect(latest!.progress).toEqual({ dock: false, trade: false, travel: false });
  });
});
