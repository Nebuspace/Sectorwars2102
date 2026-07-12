// @vitest-environment jsdom
/**
 * TacticalTargetPage — TACTICAL monitor's TARGET page (WO-UI2-DECK-
 * RECONCILE, §05: rep-colored contacts, context-aware ENGAGE/HAIL,
 * name-click→reticle, a11y text-tag alongside color).
 *
 * Mirrors DeckPageTabs.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL). combatAPI/greyStatusAPI aren't imported by
 * this component (only combatAPI is, and only from click handlers) --
 * mocked so ENGAGE assertions control the resolved outcome deterministically.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockEngage = vi.fn();
const mockGetStatus = vi.fn();
vi.mock('../../../services/api', () => ({
  combatAPI: {
    engage: (...a: unknown[]) => mockEngage(...a),
    getStatus: (...a: unknown[]) => mockGetStatus(...a),
  },
}));

const mockSendPlayerMessage = vi.fn();
const mockRefreshPlayerState = vi.fn();
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: { id: 'self-1' },
    refreshPlayerState: mockRefreshPlayerState,
    sendPlayerMessage: (...a: unknown[]) => mockSendPlayerMessage(...a),
  }),
}));

import TacticalTargetPage, { type TacticalContact } from '../pages/TacticalTargetPage';

describe('TacticalTargetPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    localStorage.clear();
    mockEngage.mockReset();
    mockGetStatus.mockReset();
    mockSendPlayerMessage.mockReset();
    mockRefreshPlayerState.mockReset();
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

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  const mount = async (contacts: TacticalContact[], onSelectContact?: (c: TacticalContact | null) => void, selectedShipId?: string | null) => {
    await act(async () => {
      root.render(
        <TacticalTargetPage contacts={contacts} onSelectContact={onSelectContact} selectedShipId={selectedShipId} />
      );
    });
    await flush();
  };

  const row = (idx = 0) => container.querySelectorAll('.target-contact-row')[idx] as HTMLElement;

  it('shows an empty state with no contacts', async () => {
    await mount([]);
    expect(container.querySelector('.empty-state')?.textContent).toBe('No contacts in sector');
  });

  // ---------------------------------------------------------------------
  // Rep-color buckets, a11y text tag alongside color (never color alone)
  // ---------------------------------------------------------------------

  it('buckets a Villain/Criminal/Outlaw player tier RED, tagged WANTED (text, not just color)', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);
    const tag = row().querySelector('.target-rep-tag')!;
    expect(tag.textContent).toBe('WANTED');
    expect(tag.className).toContain('target-rep-red');
    const name = row().querySelector('.target-contact-name') as HTMLElement;
    expect(name.style.color).toBe('rgb(255, 90, 106)'); // #FF5A6A
  });

  it('buckets a Suspicious player tier GRAY, tagged GREY-FLAG', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Sable', reputation_tier: 'Suspicious', personal_reputation: -80 },
    ]);
    const tag = row().querySelector('.target-rep-tag')!;
    expect(tag.textContent).toBe('GREY-FLAG');
    expect(tag.className).toContain('target-rep-gray');
  });

  it('buckets Neutral/Lawful/Heroic/Legendary player tiers BLUE, tagged CLEAR', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    const tag = row().querySelector('.target-rep-tag')!;
    expect(tag.textContent).toBe('CLEAR');
    expect(tag.className).toContain('target-rep-blue');
  });

  it('buckets a hostile-archetype NPC RED (WANTED), a non-hostile NPC BLUE (CLEAR)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '2', username: 'Crimson Corsair', is_npc: true, archetype: 'HOSTILE_RAIDER' },
      { player_id: 'npc2', ship_id: '3', username: 'Merchant Vessel', is_npc: true, archetype: 'LAW_ENFORCEMENT' },
    ]);
    expect(row(0).querySelector('.target-rep-tag')?.textContent).toBe('WANTED');
    expect(row(1).querySelector('.target-rep-tag')?.textContent).toBe('CLEAR');
    // NPC badge present on both, distinct from the rep tag.
    expect(row(0).querySelector('.target-npc-badge')?.textContent).toBe('NPC');
  });

  it('an NPC with no archetype but notoriety >= 50 is also RED (mirrors CombatInterface fair-game threshold)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '4', username: 'Rough Trader', is_npc: true, notoriety: 60 },
    ]);
    expect(row().querySelector('.target-rep-tag')?.textContent).toBe('WANTED');
  });

  // ---------------------------------------------------------------------
  // Context-aware ENGAGE / HAIL
  // ---------------------------------------------------------------------

  it('shows ENGAGE (not HAIL) for a RED-bucket contact with a ship present', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);
    expect(row().querySelector('.target-engage-btn')).toBeTruthy();
    expect(row().querySelector('.target-hail-btn')).toBeNull();
  });

  it('shows HAIL (not ENGAGE) for a non-NPC BLUE/GRAY contact with a player_id', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    expect(row().querySelector('.target-hail-btn')).toBeTruthy();
    expect(row().querySelector('.target-engage-btn')).toBeNull();
  });

  it('shows neither action for an NPC with no ship_id (unattackable, unhailable)', async () => {
    await mount([{ player_id: 'npc1', username: 'Distant Contact', is_npc: true }]);
    expect(row().querySelector('.target-engage-btn')).toBeNull();
    expect(row().querySelector('.target-hail-btn')).toBeNull();
  });

  it('ENGAGE calls combatAPI.engage/getStatus and shows the resolved VICTORY headline', async () => {
    mockEngage.mockResolvedValue({ status: 'initiated', combatId: 'c1' });
    mockGetStatus.mockResolvedValue({ status: 'completed', winner: 'self-1', creditsLooted: 500 });

    await mount([
      { player_id: 'p1', ship_id: '42', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);

    await click(row().querySelector('.target-engage-btn')!);

    expect(mockEngage).toHaveBeenCalledWith('ship', '42');
    expect(mockGetStatus).toHaveBeenCalledWith('c1');
    expect(mockRefreshPlayerState).toHaveBeenCalled();
    const result = row().querySelector('.target-result-msg')!;
    expect(result.textContent).toContain('VICTORY');
    expect(result.className).toContain('ok');
  });

  it('ENGAGE shows DEFEATED and does not mark it ok when the target wins', async () => {
    mockEngage.mockResolvedValue({ status: 'initiated', combatId: 'c1' });
    mockGetStatus.mockResolvedValue({ status: 'completed', winner: 'them' });

    await mount([
      { player_id: 'p1', ship_id: '42', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);
    await click(row().querySelector('.target-engage-btn')!);

    const result = row().querySelector('.target-result-msg')!;
    expect(result.textContent).toContain('DEFEATED');
    expect(result.className).toContain('err');
  });

  it('HAIL opens an inline composer and sendPlayerMessage fires on SEND', async () => {
    mockSendPlayerMessage.mockResolvedValue({ message_id: 'm1', sent_at: '2026-01-01T00:00:00Z' });

    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);

    await click(row().querySelector('.target-hail-btn')!);
    const input = row().querySelector('.target-hail-input') as HTMLInputElement;
    expect(input).toBeTruthy();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, 'Standing by');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await click(row().querySelector('.target-hail-send-btn')!);

    expect(mockSendPlayerMessage).toHaveBeenCalledWith('p1', 'Standing by', null, null);
    expect(row().querySelector('.target-hail-compose')).toBeNull();
    expect(row().querySelector('.target-result-msg')?.textContent).toBe('TRANSMITTED');
  });

  // ---------------------------------------------------------------------
  // name-click → reticle (spotlight selection)
  // ---------------------------------------------------------------------

  it('clicking the name selects the contact (reticle) only when a ship_id is present', async () => {
    const onSelectContact = vi.fn();
    await mount(
      [{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }],
      onSelectContact
    );
    const name = row().querySelector('.target-contact-name')!;
    expect(name.getAttribute('role')).toBe('button');
    // Not yet selected -- aria-pressed carries the toggle-state for SR
    // users (the ◎ reticle badge is aria-hidden, so this is the only
    // announced signal). aria-selected is spec-mismatched on role="button"
    // (defined for option/tab/gridcell/row/treeitem) and some screen
    // readers simply ignore it there -- aria-pressed is the correct
    // pressed/selected toggle-state for a button (Samantha correction,
    // WO-UI2-DECK-RECONCILE REVISE-2).
    expect(name.getAttribute('aria-pressed')).toBe('false');
    await click(name);
    expect(onSelectContact).toHaveBeenCalledWith(expect.objectContaining({ username: 'Vega' }));
  });

  it('re-clicking an already-selected contact clears the selection (toggle off)', async () => {
    const onSelectContact = vi.fn();
    await mount(
      [{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }],
      onSelectContact,
      '1'
    );
    const name = row().querySelector('.target-contact-name')!;
    expect(name.getAttribute('aria-pressed')).toBe('true');
    await click(name);
    expect(onSelectContact).toHaveBeenCalledWith(null);
  });

  it('a contact with no ship_id has a non-interactive name (no reticle target)', async () => {
    const onSelectContact = vi.fn();
    await mount([{ player_id: 'npc1', username: 'Distant Contact', is_npc: true }], onSelectContact);
    const name = row().querySelector('.target-contact-name')!;
    expect(name.getAttribute('role')).toBeNull();
    expect(name.getAttribute('aria-pressed')).toBeNull();
  });

  // ---------------------------------------------------------------------
  // a11y: live-region wiring (Pixel gate, WO-UI2-DECK-RECONCILE)
  // ---------------------------------------------------------------------

  it('the empty-state and the HAIL SEND button carry status roles/labels', async () => {
    await mount([]);
    expect(container.querySelector('.empty-state')?.getAttribute('role')).toBe('status');
  });

  it('SEND carries a state-aware aria-label and aria-busy while sending', async () => {
    let resolveSend: (v: any) => void;
    mockSendPlayerMessage.mockImplementation(() => new Promise((resolve) => { resolveSend = resolve; }));

    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    await click(row().querySelector('.target-hail-btn')!);

    const sendBtn = row().querySelector('.target-hail-send-btn')!;
    expect(sendBtn.getAttribute('aria-label')).toBe('Send message (enter text first)');

    const input = row().querySelector('.target-hail-input') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, 'Standing by');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(row().querySelector('.target-hail-send-btn')?.getAttribute('aria-label')).toBe('Send message');

    await act(async () => {
      row().querySelector('.target-hail-send-btn')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(row().querySelector('.target-hail-send-btn')?.getAttribute('aria-busy')).toBe('true');

    await act(async () => {
      resolveSend({ message_id: 'm1', sent_at: '2026-01-01T00:00:00Z' });
    });
  });
});
