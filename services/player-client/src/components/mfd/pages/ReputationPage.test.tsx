// @vitest-environment jsdom
/**
 * ReputationPage — WO-UIPC-REP-MFD-FACTION.
 *
 * Static-fetch/loading/error/empty cases mirror SalvagePage.test.tsx's seam
 * (jsdom + react-dom/client createRoot + act(), no RTL). The two live-update
 * cases need the REAL WebSocketProvider wrapping the page (not a mocked
 * useWebSocket() return value): ReputationPage is `React.memo`-wrapped and
 * takes no props, so a second `root.render()` with the same empty props
 * bails out via memo without re-invoking the component body — a synthetic
 * hook-mock reassignment would never be observed. A real Context update
 * (WebSocketProvider's own setState propagating via React Context) bypasses
 * memo's parent-prop bailout the same way GameContext.quantumHarvest.test.tsx
 * proves a live frame reaches a real consumer: inject the frame via the
 * real `websocketService` singleton's private notifyHandlers, exactly as
 * that test does.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGetReputation = vi.fn();

vi.mock('../../../services/api', () => ({
  factionAPI: {
    getReputation: (...a: unknown[]) => mockGetReputation(...a),
  },
}));

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'player-1' }, isAuthenticated: true }),
}));

const PLAYER_STATE = {
  personal_reputation: 42,
  reputation_tier: 'RESPECTED',
  military_rank: 'ENSIGN',
};

let mockPlayerState: typeof PLAYER_STATE | null = PLAYER_STATE;

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({ playerState: mockPlayerState }),
}));

import ReputationPage from './ReputationPage';
import { WebSocketProvider } from '../../../contexts/WebSocketContext';
import { websocketService, type WebSocketMessage } from '../../../services/websocket';

// websocketService is a module-level singleton; notifyHandlers is private,
// reached the same way GameContext.quantumHarvest.test.tsx / websocket.eviction.test.ts do.
const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

const makeRow = (overrides: Record<string, unknown> = {}) => ({
  faction_id: 'faction-1',
  faction_name: 'Terran Federation',
  faction_type: 'MILITARY',
  current_value: 120,
  current_level: 'TRUSTED',
  title: 'Trusted Ally',
  trade_modifier: 1.05,
  port_access_level: 2,
  combat_response: 'FRIENDLY',
  ...overrides,
});

describe('ReputationPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetReputation.mockReset();
    mockPlayerState = PLAYER_STATE;
    // No accessToken in localStorage -> WebSocketProvider's auto-connect
    // effect (`if (user && token) connect()`) stays inert -- no real socket.
    window.localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(
        <WebSocketProvider>
          <ReputationPage />
        </WebSocketProvider>
      );
    });
    await flush();
  };

  it('renders the personal STANDING/TIER/RANK fields from playerState (v1 behavior preserved)', async () => {
    mockGetReputation.mockResolvedValue([]);
    await mount();

    const values = Array.from(container.querySelectorAll('.mfd-field-value')).map((el) => el.textContent);
    expect(values).toContain('42');
    expect(values).toContain('RESPECTED');
    expect(values).toContain('ENSIGN');
  });

  it('renders one row per faction with its numeric standing', async () => {
    mockGetReputation.mockResolvedValue([
      makeRow({ faction_id: 'f1', faction_name: 'Terran Federation', current_value: 120, current_level: 'TRUSTED' }),
      makeRow({ faction_id: 'f2', faction_name: 'Shadow Syndicate', current_value: -40, current_level: 'SUSPICIOUS' }),
    ]);

    await mount();

    expect(mockGetReputation).toHaveBeenCalledTimes(1);
    const rows = container.querySelectorAll('.mfd-page-faction-row');
    expect(rows.length).toBe(2);
    expect(rows[0].querySelector('.mfd-page-faction-name')?.textContent).toBe('Terran Federation');
    expect(rows[0].querySelector('.mfd-page-faction-value')?.textContent).toBe('+120');
    expect(rows[1].querySelector('.mfd-page-faction-name')?.textContent).toBe('Shadow Syndicate');
    expect(rows[1].querySelector('.mfd-page-faction-value')?.textContent).toBe('-40');
  });

  it('shows a loading state before the fetch resolves', async () => {
    let resolveFn: (v: unknown) => void = () => {};
    mockGetReputation.mockReturnValue(new Promise((r) => { resolveFn = r; }));

    await act(async () => {
      root.render(
        <WebSocketProvider>
          <ReputationPage />
        </WebSocketProvider>
      );
    });

    expect(container.querySelector('.mfd-empty')?.textContent).toBe('LOADING…');

    await act(async () => {
      resolveFn([]);
    });
  });

  it('shows an honest empty state when no factions are returned', async () => {
    mockGetReputation.mockResolvedValue([]);
    await mount();

    expect(container.querySelector('.mfd-empty')?.textContent).toBe('NO FACTION DATA');
    expect(container.querySelector('.mfd-page-faction-row')).toBeNull();
  });

  it('shows an error line instead of crashing when the fetch fails', async () => {
    mockGetReputation.mockRejectedValue(new Error('Network down'));
    await mount();

    expect(container.querySelector('.mfd-page-warnline')?.textContent).toBe('Network down');
    expect(container.querySelector('.mfd-page-faction-row')).toBeNull();
  });

  it('shows INSUFFICIENT DATA when playerState is unavailable', async () => {
    mockGetReputation.mockResolvedValue([]);
    mockPlayerState = null;

    await mount();

    expect(container.querySelector('.mfd-insufficient')).not.toBeNull();
    expect(container.querySelector('.mfd-page-faction-row')).toBeNull();
  });

  it('patches the matching faction row live from an injected reputation_changed frame, without a refetch', async () => {
    mockGetReputation.mockResolvedValue([
      makeRow({ faction_id: 'f1', faction_name: 'Terran Federation', current_value: 120, current_level: 'TRUSTED' }),
    ]);
    await mount();
    expect(mockGetReputation).toHaveBeenCalledTimes(1);

    await act(async () => {
      svc.notifyHandlers({
        type: 'reputation_changed',
        faction_id: 'f1',
        faction_name: 'Terran Federation',
        old_level: 'TRUSTED',
        new_level: 'RESPECTED',
        old_value: 120,
        new_value: 260,
        title: 'Respected Ally',
      } as WebSocketMessage);
      await flush();
    });

    // No refetch -- the frame's own payload drove the update.
    expect(mockGetReputation).toHaveBeenCalledTimes(1);
    const row = container.querySelector('.mfd-page-faction-row')!;
    expect(row.querySelector('.mfd-page-faction-value')?.textContent).toBe('+260');
    expect(row.querySelector('.mfd-page-faction-level')?.textContent).toBe('RESPECTED');
  });

  it('adds a distinct TEAM standing badge from an injected team_reputation_changed frame, without touching the personal value', async () => {
    mockGetReputation.mockResolvedValue([
      makeRow({ faction_id: 'f1', faction_name: 'Terran Federation', current_value: 120, current_level: 'TRUSTED' }),
    ]);
    await mount();

    await act(async () => {
      svc.notifyHandlers({
        type: 'team_reputation_changed',
        team_id: 'team-1',
        method: 'average',
        timestamp: '2026-07-10T00:00:00Z',
        faction_id: 'f1',
        faction_name: 'Terran Federation',
        old_level: 'NEUTRAL',
        new_level: 'RECOGNIZED',
        old_value: 10,
        new_value: 55,
      } as WebSocketMessage);
      await flush();
    });

    const row = container.querySelector('.mfd-page-faction-row')!;
    // Personal reading is untouched by the team-aggregate frame.
    expect(row.querySelector('.mfd-page-faction-value')?.textContent).toBe('+120');
    expect(row.querySelector('.mfd-page-faction-level')?.textContent).toBe('TRUSTED');
    expect(row.querySelector('.mfd-page-faction-team')?.textContent).toBe('TEAM +55 (RECOGNIZED)');
  });

  it('ignores an unrelated frame type (no row mutation, no crash)', async () => {
    mockGetReputation.mockResolvedValue([
      makeRow({ faction_id: 'f1', faction_name: 'Terran Federation', current_value: 120, current_level: 'TRUSTED' }),
    ]);
    await mount();

    await act(async () => {
      expect(() => {
        svc.notifyHandlers({ type: 'chat_message', content: 'hi' } as unknown as WebSocketMessage);
      }).not.toThrow();
      await flush();
    });

    const row = container.querySelector('.mfd-page-faction-row')!;
    expect(row.querySelector('.mfd-page-faction-value')?.textContent).toBe('+120');
  });
});
