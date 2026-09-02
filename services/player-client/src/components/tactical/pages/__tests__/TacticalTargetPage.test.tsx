// @vitest-environment jsdom
/**
 * TacticalTargetPage — TACTICAL monitor's TARGET page (WO-UI2-DECK-
 * RECONCILE §05). Covers: empty state, contact-name/rep-bucket rendering,
 * the popup-menu trigger gate (menuHasItems) and its ENGAGE/APPROACH/
 * HAIL/TRADE item composition, reticle-select sync, the HAIL send flow
 * (success + failure), the TRADE overlay open/close, and the full
 * handleEngage branch matrix (validation failure, rate-limit exceeded,
 * victory w/ loot, defeat, non-initiated response, and a thrown API
 * error). ContactActionMenu / HailComposeDialog / PlayerTradeDesk each
 * already carry their own dedicated test files (ContactActionMenu.test.tsx,
 * HailComposeDialog.test.tsx, trade/__tests__/PlayerTradeDesk.test.tsx) —
 * stubbed here as thin data-testid shells, same convention as
 * TeamManager.test.tsx's ResourceSharing/TeamChat stubs. WindshieldTableau
 * is stubbed to just its two pure re-exports (distancePx/REFERENCE_BAND)
 * to avoid pulling in that component's full canvas/import tree for a page
 * that only consumes those two named exports.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import React from 'react';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const { useGameMock, useWindshieldFlightMock, combatEngageMock, combatGetStatusMock, sendPlayerMessageMock, refreshPlayerStateMock, approachMock } =
  vi.hoisted(() => ({
    useGameMock: vi.fn(),
    useWindshieldFlightMock: vi.fn(),
    combatEngageMock: vi.fn(),
    combatGetStatusMock: vi.fn(),
    sendPlayerMessageMock: vi.fn(),
    refreshPlayerStateMock: vi.fn(),
    approachMock: vi.fn(),
  }));

vi.mock('../../../../contexts/GameContext', () => ({
  useGame: useGameMock,
}));

vi.mock('../../../../contexts/WindshieldFlightContext', () => ({
  useWindshieldFlight: useWindshieldFlightMock,
}));

vi.mock('../../../../services/api', () => ({
  combatAPI: {
    engage: combatEngageMock,
    getStatus: combatGetStatusMock,
  },
}));

vi.mock('../../WindshieldTableau', () => ({
  distancePx: (a: any, b: any, band: any) =>
    Math.hypot(((a.xPct - b.xPct) / 100) * band.widthPx, ((a.yPct - b.yPct) / 100) * band.heightPx),
  REFERENCE_BAND: { widthPx: 1440, heightPx: 334.7, remPx: 18.09 },
}));

vi.mock('../../ContactActionMenu', () => ({
  __esModule: true,
  default: ({ items, label, onClose }: any) => (
    <div data-testid="contact-action-menu" aria-label={label}>
      {items.map((it: any) => (
        <button key={it.key} data-testid={`menu-item-${it.key}`} title={it.title} onClick={it.onSelect}>
          {it.label}
        </button>
      ))}
      <button data-testid="menu-close" onClick={onClose}>
        close
      </button>
    </div>
  ),
}));

vi.mock('../../HailComposeDialog', () => ({
  __esModule: true,
  default: ({ contactName, value, onChange, onSend, onCancel, busy, error }: any) => (
    <div data-testid="hail-compose-dialog">
      <span data-testid="hail-contact-name">{contactName}</span>
      <input
        data-testid="hail-input"
        value={value}
        disabled={busy}
        onChange={(e) => onChange((e.target as HTMLInputElement).value)}
      />
      <button data-testid="hail-send" disabled={busy} onClick={onSend}>
        Send
      </button>
      <button data-testid="hail-cancel" onClick={onCancel}>
        Cancel
      </button>
      {error && <span data-testid="hail-error">{error}</span>}
    </div>
  ),
}));

vi.mock('../../../trade/PlayerTradeDesk', () => ({
  __esModule: true,
  default: ({ targetPlayerId, myPlayerId, onClose }: any) => (
    <div data-testid="player-trade-desk" data-target={targetPlayerId} data-mine={myPlayerId}>
      <button data-testid="trade-close" onClick={onClose}>
        close
      </button>
    </div>
  ),
}));

import TacticalTargetPage, {
  formatTacticalTargetEngageError,
  formatTacticalTargetHailError,
  type TacticalContact,
} from '../TacticalTargetPage';

let container: HTMLDivElement;
let root: Root;

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
};

const setInputValue = (el: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
};

const render = (ui: React.ReactElement) => {
  act(() => {
    root.render(ui);
  });
};

const PLAYER_STATE = { id: 'player-self', current_sector_id: 'sec-1', turns: 100 };

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);

  localStorage.clear();
  vi.clearAllMocks();

  useGameMock.mockReturnValue({
    playerState: PLAYER_STATE,
    refreshPlayerState: refreshPlayerStateMock,
    sendPlayerMessage: sendPlayerMessageMock,
  });
  useWindshieldFlightMock.mockReturnValue({
    contactPositions: new Map(),
    shipPos: null,
    engageRangeEm: 10,
    approach: approachMock,
  });
});

const teardown = () => {
  act(() => {
    root.unmount();
  });
  container.remove();
};

const engageableContacts = (): TacticalContact[] => [
  { id: 'c1', ship_id: '1001', username: 'Foe', reputation_tier: 'Villain', player_id: 'u1' },
];

const openEngage = (contacts: TacticalContact[]) => {
  useWindshieldFlightMock.mockReturnValue({
    contactPositions: new Map([[String(contacts[0].ship_id), { xPct: 0, yPct: 50 }]]),
    shipPos: { xPct: 0, yPct: 50 },
    engageRangeEm: 100,
    approach: approachMock,
  });
  render(<TacticalTargetPage contacts={contacts} />);
  const name = container.querySelector('.target-contact-name') as HTMLElement;
  act(() => {
    name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  return container.querySelector('[data-testid="menu-item-engage"]') as HTMLButtonElement;
};

describe('TacticalTargetPage', () => {
  it('renders the empty state when there are no contacts', () => {
    render(<TacticalTargetPage contacts={[]} />);
    const status = container.querySelector('[role="status"]');
    expect(status?.textContent).toBe('No contacts in sector');
    teardown();
  });

  it('renders a list with one listitem per contact', () => {
    const contacts: TacticalContact[] = [
      { id: 'c1', username: 'Alice', reputation_tier: 'Neutral' },
      { id: 'c2', username: 'Bob', reputation_tier: 'Neutral' },
    ];
    render(<TacticalTargetPage contacts={contacts} />);
    const list = container.querySelector('[role="list"][aria-label="Sector contacts"]');
    expect(list).not.toBeNull();
    expect(container.querySelectorAll('[role="listitem"]')).toHaveLength(2);
    teardown();
  });

  it('falls back through username -> name -> UNKNOWN CONTACT, and prefixes a military rank', () => {
    const contacts: TacticalContact[] = [
      { id: 'c1', name: 'Named Only' },
      { id: 'c2' },
      { id: 'c3', username: 'Ranked', military_rank: 'commander' },
    ];
    render(<TacticalTargetPage contacts={contacts} />);
    const text = container.textContent || '';
    expect(text).toContain('Named Only');
    expect(text).toContain('UNKNOWN CONTACT');
    expect(text).toContain('COMMANDER Ranked');
    teardown();
  });

  it('renders pinned medal pin and count on a sector contact row (LEG-2666)', () => {
    const contacts: TacticalContact[] = [
      {
        id: 'c1',
        player_id: 'p1',
        username: 'Medalist',
        pinned_medal_id: 'star_bronze',
        medal_count: 4,
      },
    ];
    render(<TacticalTargetPage contacts={contacts} />);
    const plate = container.querySelector(
      '[role="listitem"] [data-testid="player-name-plate"]',
    ) as HTMLElement;
    expect(plate).not.toBeNull();
    expect(plate.getAttribute('data-pinned-medal')).toBe('star_bronze');
    expect(plate.querySelector('[data-testid="player-name-plate-medal"]')?.textContent).toBe('🏅');
    expect(plate.querySelector('[data-testid="player-name-plate-count"]')?.textContent).toBe('4');
    teardown();
  });

  it('buckets a red-tier player contact and carries the record into the name title', () => {
    const contacts: TacticalContact[] = [
      { id: 'c1', user_id: 'u1', username: 'Villainous', reputation_tier: 'Villain', personal_reputation: -40, player_id: 'u1' },
    ];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    expect(name.style.color).toBe('rgb(255, 90, 106)'); // #FF5A6A
    expect(name.title).toBe('WANTED · Villain (-40)');
    teardown();
  });

  it('buckets a hostile NPC red and a non-hostile NPC blue via archetype/notoriety', () => {
    const contacts: TacticalContact[] = [
      { id: 'npc1', is_npc: true, username: 'Raider', archetype: 'HOSTILE_RAIDER' },
      { id: 'npc2', is_npc: true, username: 'Trader', archetype: 'FREIGHT_HAULER', notoriety: 0 },
    ];
    render(<TacticalTargetPage contacts={contacts} />);
    const names = container.querySelectorAll('.target-contact-name') as NodeListOf<HTMLElement>;
    expect(names[0].title).toBe('WANTED · hostile raider');
    expect(names[1].title).toBe('CLEAR · freight hauler');
    teardown();
  });

  it('gives no clickable trigger to a shipless, unhailable NPC contact', () => {
    const contacts: TacticalContact[] = [{ id: 'npc1', is_npc: true, username: 'Ghost' }];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    expect(name.getAttribute('role')).toBeNull();
    expect(name.getAttribute('tabindex')).toBeNull();
    expect(name.getAttribute('aria-haspopup')).toBeNull();
    teardown();
  });

  it('opens and toggle-closes the popup menu on the name trigger, syncing reticle-select', () => {
    const onSelectContact = vi.fn();
    const contacts: TacticalContact[] = [
      { id: 'c1', user_id: 'u1', player_id: 'u1', ship_id: 'ship-1', username: 'Target', reputation_tier: 'Neutral' },
    ];
    render(<TacticalTargetPage contacts={contacts} onSelectContact={onSelectContact} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    expect(name.getAttribute('role')).toBe('button');
    expect(name.getAttribute('aria-expanded')).toBe('false');

    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="contact-action-menu"]')).not.toBeNull();
    expect(name.getAttribute('aria-expanded')).toBe('true');
    expect(onSelectContact).toHaveBeenCalledWith(contacts[0]);

    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="contact-action-menu"]')).toBeNull();
    expect(onSelectContact).toHaveBeenLastCalledWith(null);
    teardown();
  });

  it('shows the selected badge when selectedShipId matches the contact', () => {
    const contacts: TacticalContact[] = [{ id: 'c1', ship_id: 'ship-1', username: 'Picked', player_id: 'u1' }];
    render(<TacticalTargetPage contacts={contacts} selectedShipId="ship-1" />);
    expect(container.querySelector('.target-selected-badge')).not.toBeNull();
    teardown();
  });

  it('offers APPROACH (not ENGAGE) for a ship-bearing contact outside engage range', () => {
    useWindshieldFlightMock.mockReturnValue({
      contactPositions: new Map([['ship-1', { xPct: 90, yPct: 50 }]]),
      shipPos: { xPct: 0, yPct: 50 },
      engageRangeEm: 1,
      approach: approachMock,
    });
    const contacts: TacticalContact[] = [{ id: 'c1', ship_id: 'ship-1', username: 'Far', player_id: 'u1' }];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="menu-item-approach"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="menu-item-engage"]')).toBeNull();

    const approachBtn = container.querySelector('[data-testid="menu-item-approach"]') as HTMLButtonElement;
    act(() => {
      approachBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(approachMock).toHaveBeenCalledWith('ship-1');
    teardown();
  });

  it('offers ENGAGE (not APPROACH) once the contact is within engage range', () => {
    useWindshieldFlightMock.mockReturnValue({
      contactPositions: new Map([['ship-1', { xPct: 1, yPct: 50 }]]),
      shipPos: { xPct: 0, yPct: 50 },
      engageRangeEm: 100,
      approach: approachMock,
    });
    const contacts: TacticalContact[] = [{ id: 'c1', ship_id: 'ship-1', username: 'Close', player_id: 'u1' }];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="menu-item-engage"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="menu-item-approach"]')).toBeNull();
    teardown();
  });

  it('tags a blue/clean-target ENGAGE item with turn-cost preview and rep-cost warning tooltip', () => {
    useWindshieldFlightMock.mockReturnValue({
      contactPositions: new Map([['ship-1', { xPct: 0, yPct: 50 }]]),
      shipPos: { xPct: 0, yPct: 50 },
      engageRangeEm: 100,
      approach: approachMock,
    });
    const contacts: TacticalContact[] = [
      { id: 'c1', ship_id: 'ship-1', username: 'Clean', reputation_tier: 'Neutral', player_id: 'u1', attack_turn_cost: 8 },
    ];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const engageBtn = container.querySelector('[data-testid="menu-item-engage"]') as HTMLButtonElement;
    const warning = 'Engaging a clean target flags you as an outlaw: -100 rep + 1h grey';
    expect(engageBtn.title).toContain('Costs 8 turns');
    expect(engageBtn.title).toContain('You have 100 turns');
    expect(engageBtn.title).toContain(warning);
    teardown();
  });

  it('offers HAIL and TRADE only for a non-NPC contact carrying a player_id', () => {
    const contacts: TacticalContact[] = [
      { id: 'c1', player_id: 'u1', username: 'Hailable' },
      { id: 'c2', is_npc: true, archetype: 'FREIGHT_HAULER', username: 'NoHail' },
    ];
    render(<TacticalTargetPage contacts={contacts} />);
    const names = container.querySelectorAll('.target-contact-name') as NodeListOf<HTMLElement>;

    act(() => {
      names[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="menu-item-hail"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="menu-item-trade"]')).not.toBeNull();

    expect(names[1].getAttribute('role')).toBeNull();
    teardown();
  });

  it('sends a hail and shows TRANSMITTED once composing ends', async () => {
    sendPlayerMessageMock.mockResolvedValue({ message_id: 'm1', sent_at: 'now' });
    const contacts: TacticalContact[] = [{ id: 'c1', player_id: 'u1', username: 'Pen Pal' }];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const hailBtn = container.querySelector('[data-testid="menu-item-hail"]') as HTMLButtonElement;
    act(() => {
      hailBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="hail-compose-dialog"]')).not.toBeNull();

    const input = container.querySelector('[data-testid="hail-input"]') as HTMLInputElement;
    act(() => {
      setInputValue(input, 'ahoy');
    });
    const sendBtn = container.querySelector('[data-testid="hail-send"]') as HTMLButtonElement;
    await act(async () => {
      sendBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    expect(sendPlayerMessageMock).toHaveBeenCalledWith('u1', 'ahoy', null, null);
    expect(container.querySelector('[data-testid="hail-compose-dialog"]')).toBeNull();
    const status = container.querySelector('.target-result-msg.ok');
    expect(status?.textContent).toBe('TRANSMITTED');
    teardown();
  });

  it('keeps the dialog open and shows the error inside it when a hail fails', async () => {
    sendPlayerMessageMock.mockRejectedValue(new Error('relay down'));
    const contacts: TacticalContact[] = [{ id: 'c1', player_id: 'u1', username: 'Pen Pal' }];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    act(() => {
      (container.querySelector('[data-testid="menu-item-hail"]') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    const input = container.querySelector('[data-testid="hail-input"]') as HTMLInputElement;
    act(() => {
      setInputValue(input, 'ahoy');
    });
    await act(async () => {
      (container.querySelector('[data-testid="hail-send"]') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    await flush();

    expect(container.querySelector('[data-testid="hail-compose-dialog"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="hail-error"]')?.textContent).toBe('relay down');
    expect(container.querySelector('.target-result-msg')).toBeNull();
    teardown();
  });

  it('opens PlayerTradeDesk with the right target/self ids from TRADE, and closes it', () => {
    const contacts: TacticalContact[] = [{ id: 'c1', player_id: 'trader-1', username: 'Merchant' }];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    act(() => {
      (container.querySelector('[data-testid="menu-item-trade"]') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    const desk = container.querySelector('[data-testid="player-trade-desk"]') as HTMLElement;
    expect(desk.dataset.target).toBe('trader-1');
    expect(desk.dataset.mine).toBe('player-self');

    act(() => {
      (container.querySelector('[data-testid="trade-close"]') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true })
      );
    });
    expect(container.querySelector('[data-testid="player-trade-desk"]')).toBeNull();
    teardown();
  });

  it('rejects engage with a validation error and never calls the combat API', async () => {
    const contacts = engageableContacts();
    contacts[0].ship_id = 'not-a-valid-id!!';
    const engageBtn = openEngage(contacts);
    await act(async () => {
      engageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(combatEngageMock).not.toHaveBeenCalled();
    expect(container.querySelector('.target-result-msg.err')?.textContent).toBe('Invalid target ID format');
    teardown();
  });

  it('blocks engage once the rate limit is exhausted and logs the audit event', async () => {
    const now = Date.now();
    localStorage.setItem(
      'rate_limit_combat_player-self',
      JSON.stringify([now, now, now, now, now])
    );
    const engageBtn = openEngage(engageableContacts());
    await act(async () => {
      engageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(combatEngageMock).not.toHaveBeenCalled();
    expect(container.querySelector('.target-result-msg.err')?.textContent).toBe(
      'Too many combat attempts — wait before engaging again.'
    );
    teardown();
  });

  it('shows ENGAGING… while the combat call is in flight, then VICTORY with looted credits on a self win', async () => {
    let resolveEngage!: (v: any) => void;
    combatEngageMock.mockReturnValue(new Promise((res) => { resolveEngage = res; }));
    combatGetStatusMock.mockResolvedValue({
      status: 'completed',
      winner: 'player-self',
      creditsLooted: 2500,
    });
    const engageBtn = openEngage(engageableContacts());
    act(() => {
      engageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('.target-result-msg')?.textContent).toBe('ENGAGING…');

    await act(async () => {
      resolveEngage({ status: 'initiated', combatId: 'combat-1' });
    });
    await flush();

    expect(combatEngageMock).toHaveBeenCalledWith('ship', '1001');
    expect(refreshPlayerStateMock).toHaveBeenCalled();
    expect(container.querySelector('.target-result-msg.ok')?.textContent).toBe('VICTORY — ₡2,500 looted');
    expect(localStorage.getItem('rate_limit_combat_player-self')).toBeNull();
    teardown();
  });

  it('shows DEFEATED when the opponent wins', async () => {
    combatEngageMock.mockResolvedValue({ status: 'initiated', combatId: 'combat-2' });
    combatGetStatusMock.mockResolvedValue({ status: 'completed', winner: 'the-other-guy' });
    const engageBtn = openEngage(engageableContacts());
    await act(async () => {
      engageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('.target-result-msg.err')?.textContent).toBe('DEFEATED');
    teardown();
  });

  it('shows the server message when combat initiation itself is rejected', async () => {
    combatEngageMock.mockResolvedValue({ status: 'rejected', message: 'Target already destroyed' });
    const engageBtn = openEngage(engageableContacts());
    await act(async () => {
      engageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(combatGetStatusMock).not.toHaveBeenCalled();
    expect(container.querySelector('.target-result-msg.err')?.textContent).toBe('Target already destroyed');
    teardown();
  });

  it('shows a generic combat-system-error message when the engage call throws', async () => {
    combatEngageMock.mockRejectedValue(new Error('network blip'));
    const engageBtn = openEngage(engageableContacts());
    await act(async () => {
      engageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('.target-result-msg.err')?.textContent).toBe('network blip');
    teardown();
  });
});

describe('TacticalTargetPage TypeError densify (LEG-3261)', () => {
  it('formatTacticalTargetHailError falls back on TypeError network collapse', () => {
    const text = formatTacticalTargetHailError(new TypeError('Failed to fetch'));
    expect(text).toBe('TRANSMISSION FAILED');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('formatTacticalTargetHail/Engage fall back on axios Network Error (LEG-3304)', () => {
    expect(formatTacticalTargetHailError(new Error('Network Error'))).toBe('TRANSMISSION FAILED');
    expect(formatTacticalTargetHailError(new Error('Failed to fetch'))).toBe('TRANSMISSION FAILED');
    expect(formatTacticalTargetHailError(new Error('   '))).toBe('TRANSMISSION FAILED');
    expect(formatTacticalTargetEngageError(new Error('Network Error'))).toBe('Combat system error — try again.');
    expect(formatTacticalTargetEngageError(new Error('Failed to fetch'))).toBe('Combat system error — try again.');
    expect(formatTacticalTargetEngageError(new Error('hostile lock'))).toBe('hostile lock');
  });

  it('formatTacticalTargetEngageError falls back on TypeError network collapse', () => {
    const text = formatTacticalTargetEngageError(new TypeError('Failed to fetch'));
    expect(text).toBe('Combat system error — try again.');
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('hail TypeError surfaces TRANSMISSION FAILED without Failed to fetch / TypeError in DOM', async () => {
    sendPlayerMessageMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const contacts: TacticalContact[] = [{ id: 'c1', player_id: 'u1', username: 'Pen Pal' }];
    render(<TacticalTargetPage contacts={contacts} />);
    const name = container.querySelector('.target-contact-name') as HTMLElement;
    act(() => {
      name.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    act(() => {
      (container.querySelector('[data-testid="menu-item-hail"]') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    const input = container.querySelector('[data-testid="hail-input"]') as HTMLInputElement;
    act(() => {
      setInputValue(input, 'ahoy');
    });
    await act(async () => {
      (container.querySelector('[data-testid="hail-send"]') as HTMLButtonElement).dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    });
    await flush();

    const err = container.querySelector('[data-testid="hail-error"]');
    expect(err?.textContent).toBe('TRANSMISSION FAILED');
    expect(err?.textContent).not.toMatch(/Failed to fetch/i);
    expect(err?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    teardown();
  });

  it('engage TypeError surfaces combat fallback without Failed to fetch / TypeError in DOM', async () => {
    combatEngageMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const engageBtn = openEngage(engageableContacts());
    await act(async () => {
      engageBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    const msg = container.querySelector('.target-result-msg.err');
    expect(msg?.textContent).toBe('Combat system error — try again.');
    expect(msg?.textContent).not.toMatch(/Failed to fetch/i);
    expect(msg?.textContent).not.toMatch(/TypeError/i);
    expect(container.textContent).not.toMatch(/Failed to fetch/i);
    expect(container.textContent).not.toMatch(/TypeError/i);
    teardown();
  });
});
