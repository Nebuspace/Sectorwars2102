// @vitest-environment jsdom
/**
 * TacticalTargetPage — TACTICAL monitor's TARGET page (WO-UI2-DECK-
 * RECONCILE, §05, rewired by WO-TACTICAL-POPUP: rep-colored contacts,
 * clicking the NAME opens ContactActionMenu with whichever of ENGAGE/HAIL
 * apply, HAIL opens HailComposeDialog, reticle-select stays decoupled).
 *
 * Mirrors DeckPageTabs.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL) — the SAME harness this file already used
 * pre-WO-TACTICAL-POPUP. ContactActionMenu/HailComposeDialog both portal
 * to document.body (ConfirmDialog's own idiom), so their content is
 * queried off `document.body`, not `container`, even though the row that
 * opened them lives inside `container`.
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

  const keydown = async (key: string, target: EventTarget = document) => {
    await act(async () => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
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
  const name = (idx = 0) => row(idx).querySelector('.target-contact-name') as HTMLElement;
  // ContactActionMenu/HailComposeDialog both portal to document.body.
  const menu = () => document.body.querySelector('.contact-action-menu');
  const menuItem = (variant: 'engage' | 'hail') =>
    menu()?.querySelector(`.contact-action-menu-item-${variant}`) as HTMLElement | null;
  const hailDialog = () => document.body.querySelector('.confirm-dialog-panel[aria-label^="Hail message"]');
  const hailInput = () => hailDialog()?.querySelector('.target-hail-input') as HTMLInputElement | null;
  const hailSendBtn = () => hailDialog()?.querySelector('.confirm-dialog-btn.confirm') as HTMLElement | null;

  it('shows an empty state with no contacts', async () => {
    await mount([]);
    expect(container.querySelector('.empty-state')?.textContent).toBe('No contacts in sector');
  });

  // ---------------------------------------------------------------------
  // Rep-color buckets — the permanent visible text tag/NPC badge were
  // retired with the row-strip (WO-TACTICAL-POPUP); the bucket now
  // survives in the name color (unchanged) and the `title` hover record.
  // ---------------------------------------------------------------------

  it('buckets a Villain/Criminal/Outlaw player tier RED (name color + title record)', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);
    expect(name().style.color).toBe('rgb(255, 90, 106)'); // #FF5A6A
    expect(name().getAttribute('title')).toContain('WANTED');
  });

  it('buckets a Suspicious player tier GRAY, title record says GREY-FLAG', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Sable', reputation_tier: 'Suspicious', personal_reputation: -80 },
    ]);
    expect(name().getAttribute('title')).toContain('GREY-FLAG');
  });

  it('buckets Neutral/Lawful/Heroic/Legendary player tiers BLUE, title record says CLEAR', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    expect(name().getAttribute('title')).toContain('CLEAR');
  });

  it('buckets a hostile-archetype NPC RED, a non-hostile NPC BLUE (and non-interactive with no actions)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '2', username: 'Crimson Corsair', is_npc: true, archetype: 'HOSTILE_RAIDER' },
      { player_id: 'npc2', ship_id: '3', username: 'Merchant Vessel', is_npc: true, archetype: 'LAW_ENFORCEMENT' },
    ]);
    expect(name(0).getAttribute('title')).toContain('WANTED');
    expect(name(1).getAttribute('title')).toContain('CLEAR');
    // Hostile NPC can ENGAGE -> interactive trigger; peaceful NPC has
    // neither ENGAGE (not is_npc red... it's blue) nor HAIL (is_npc) --
    // no menu to offer, so its name is not a menu trigger.
    expect(name(0).getAttribute('role')).toBe('button');
    expect(name(1).getAttribute('role')).toBeNull();
  });

  it('an NPC with no archetype but notoriety >= 50 is also RED (mirrors CombatInterface fair-game threshold)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '4', username: 'Rough Trader', is_npc: true, notoriety: 60 },
    ]);
    expect(name().getAttribute('title')).toContain('WANTED');
  });

  // ---------------------------------------------------------------------
  // ContactActionMenu composition — canEngage/canHail are independent
  // predicates now (WO-TACTICAL-POPUP), not an either/or button row.
  // ---------------------------------------------------------------------

  it('menu shows ENGAGE only for a hostile NPC (never hailable — is_npc excludes HAIL)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '2', username: 'Crimson Corsair', is_npc: true, archetype: 'HOSTILE_RAIDER' },
    ]);
    await click(name());
    expect(menuItem('engage')).toBeTruthy();
    expect(menuItem('hail')).toBeNull();
  });

  it('menu shows HAIL only for a non-NPC BLUE contact', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    await click(name());
    expect(menuItem('hail')).toBeTruthy();
    expect(menuItem('engage')).toBeNull();
  });

  it('menu shows BOTH ENGAGE and HAIL for a hostile PLAYER contact (the old inline row could only ever show one)', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);
    await click(name());
    expect(menuItem('engage')).toBeTruthy();
    expect(menuItem('hail')).toBeTruthy();
    expect(menu()?.querySelectorAll('.contact-action-menu-item').length).toBe(2);
  });

  it('shows no menu for an NPC with no ship_id (unattackable, unhailable)', async () => {
    await mount([{ player_id: 'npc1', username: 'Distant Contact', is_npc: true }]);
    expect(name().getAttribute('role')).toBeNull();
    await click(name());
    expect(menu()).toBeNull();
  });

  it('trigger-gating fix: a hail-only contact with NO ship_id still opens the menu (old ship_id-only gate stranded it)', async () => {
    await mount([{ player_id: 'p1', username: 'Comms Only', reputation_tier: 'Lawful' }]);
    expect(name().getAttribute('role')).toBe('button');
    await click(name());
    expect(menuItem('hail')).toBeTruthy();
  });

  it('the row renders name-only -- no NPC badge, rep-tag chip, legend, or inline action buttons anywhere in the DOM', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '2', username: 'Crimson Corsair', is_npc: true, archetype: 'HOSTILE_RAIDER' },
    ]);
    expect(container.querySelector('.target-npc-badge')).toBeNull();
    expect(container.querySelector('.target-rep-tag')).toBeNull();
    expect(container.querySelector('.target-legend')).toBeNull();
    expect(container.querySelector('.target-contact-actions')).toBeNull();
    expect(container.querySelector('.target-engage-btn')).toBeNull();
    expect(container.querySelector('.target-hail-btn')).toBeNull();
  });

  it('Enter and Space (not just click) open the menu from the trigger', async () => {
    await mount([{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }]);

    await keydown('Enter', name());
    expect(menu()).toBeTruthy();

    await keydown('Escape'); // close it back out before the next probe
    expect(menu()).toBeNull();

    await keydown(' ', name());
    expect(menu()).toBeTruthy();
  });

  it('an outside click closes the menu', async () => {
    await mount([{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }]);
    await click(name());
    expect(menu()).toBeTruthy();

    // ContactActionMenu ignores dismissals within 150ms of opening (guards
    // against the same gesture that opened it); wait it out for a genuine
    // outside interaction.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
    });
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    await flush();

    expect(menu()).toBeNull();
  });

  // ---------------------------------------------------------------------
  // ENGAGE flow (via the menu)
  // ---------------------------------------------------------------------

  it('ENGAGE calls combatAPI.engage/getStatus, closes the menu, and shows the resolved VICTORY headline', async () => {
    mockEngage.mockResolvedValue({ status: 'initiated', combatId: 'c1' });
    mockGetStatus.mockResolvedValue({ status: 'completed', winner: 'self-1', creditsLooted: 500 });

    await mount([
      { player_id: 'npc1', ship_id: '42', username: 'Crimson Corsair', is_npc: true, archetype: 'HOSTILE_RAIDER' },
    ]);
    await click(name());
    await click(menuItem('engage')!);

    expect(menu()).toBeNull(); // menu closes the instant an item is chosen
    expect(mockEngage).toHaveBeenCalledWith('ship', '42');
    expect(mockGetStatus).toHaveBeenCalledWith('c1');
    expect(mockRefreshPlayerState).toHaveBeenCalled();
    const result = row().querySelector('.target-result-msg.ok')!;
    expect(result.textContent).toContain('VICTORY');
  });

  it('ENGAGE shows DEFEATED and does not mark it ok when the target wins', async () => {
    mockEngage.mockResolvedValue({ status: 'initiated', combatId: 'c1' });
    mockGetStatus.mockResolvedValue({ status: 'completed', winner: 'them' });

    await mount([
      { player_id: 'npc1', ship_id: '42', username: 'Crimson Corsair', is_npc: true, archetype: 'HOSTILE_RAIDER' },
    ]);
    await click(name());
    await click(menuItem('engage')!);

    const result = row().querySelector('.target-result-msg.err')!;
    expect(result.textContent).toContain('DEFEATED');
  });

  // ---------------------------------------------------------------------
  // HAIL flow (menu -> HailComposeDialog, portal'd to document.body)
  // ---------------------------------------------------------------------

  it('HAIL opens a dialog, closes the menu, and sendPlayerMessage fires on Send', async () => {
    mockSendPlayerMessage.mockResolvedValue({ message_id: 'm1', sent_at: '2026-01-01T00:00:00Z' });

    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    await click(name());
    await click(menuItem('hail')!);

    expect(menu()).toBeNull();
    const input = hailInput()!;
    expect(input).toBeTruthy();
    // Mount-focus lands on the input (WAI-ARIA dialog pattern).
    expect(document.activeElement).toBe(input);

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, 'Standing by');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await click(hailSendBtn()!);

    expect(mockSendPlayerMessage).toHaveBeenCalledWith('p1', 'Standing by', null, null);
    expect(hailDialog()).toBeNull();
    expect(row().querySelector('.target-result-msg.ok')?.textContent).toBe('TRANSMITTED');
  });

  it('a failed HAIL keeps the dialog open and shows the error inside it', async () => {
    mockSendPlayerMessage.mockRejectedValue(new Error('link down'));

    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' },
    ]);
    await click(name());
    await click(menuItem('hail')!);

    const input = hailInput()!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, 'hello');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(hailSendBtn()!);

    expect(hailDialog()).toBeTruthy(); // stays open on failure so the user can retry
    expect(hailDialog()?.querySelector('.target-result-msg.err')?.textContent).toBe('link down');
    // Not duplicated in the row while still composing.
    expect(row().querySelector('.target-result-msg.err')).toBeNull();
  });

  it('Escape cancels the HAIL dialog', async () => {
    await mount([{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }]);
    await click(name());
    await click(menuItem('hail')!);
    expect(hailDialog()).toBeTruthy();

    await keydown('Escape');
    expect(hailDialog()).toBeNull();
  });

  // ---------------------------------------------------------------------
  // Reticle-select stays decoupled from menu open/close (WO-TACTICAL-
  // POPUP file header) -- both fire off the same click when both apply;
  // a hail-only contact with no ship_id opens the menu but never selects.
  // ---------------------------------------------------------------------

  it('clicking the name both opens the menu and selects the reticle when a ship_id is present', async () => {
    const onSelectContact = vi.fn();
    await mount(
      [{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }],
      onSelectContact
    );
    // Selection is announced via the visual ◎ badge (fed back in as the
    // selectedShipId prop by the real parent), not aria-pressed on this
    // menu-button trigger -- Pixel REVISE. Not re-asserted here since this
    // mock doesn't feed selectedShipId back in; the badge-appears case is
    // covered below by mounting pre-selected.
    expect(row().querySelector('.target-selected-badge')).toBeNull();
    await click(name());
    expect(onSelectContact).toHaveBeenCalledWith(expect.objectContaining({ username: 'Vega' }));
    expect(menu()).toBeTruthy();
  });

  it('re-clicking an open-menu contact closes the menu and clears the selection (toggle off, same click drives both)', async () => {
    const onSelectContact = vi.fn();
    await mount(
      [{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }],
      onSelectContact
    );
    await click(name()); // opens + selects
    expect(menu()).toBeTruthy();
    expect(onSelectContact).toHaveBeenLastCalledWith(expect.objectContaining({ username: 'Vega' }));

    await click(name()); // closes + deselects -- same click drives both axes
    expect(menu()).toBeNull();
    expect(onSelectContact).toHaveBeenLastCalledWith(null);
  });

  it('a contact already selected externally (reticle-select elsewhere) still OPENS on click rather than deselecting -- menu-open state, not the selected prop, drives the toggle', async () => {
    const onSelectContact = vi.fn();
    await mount(
      [{ player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' }],
      onSelectContact,
      '1' // pre-selected via the selectedShipId prop, menu never opened yet
    );
    // Selection is real (the ◎ badge is already showing) even though it's
    // not announced via aria-pressed on this menu-button trigger.
    expect(row().querySelector('.target-selected-badge')).toBeTruthy();
    expect(name().getAttribute('aria-pressed')).toBeNull();
    await click(name());
    expect(menu()).toBeTruthy();
    expect(onSelectContact).toHaveBeenLastCalledWith(expect.objectContaining({ username: 'Vega' }));
  });

  it('a hail-only contact with no ship_id opens the menu but never fires onSelectContact (no reticle target to select)', async () => {
    const onSelectContact = vi.fn();
    await mount([{ player_id: 'p1', username: 'Comms Only', reputation_tier: 'Lawful' }], onSelectContact);
    expect(name().getAttribute('aria-pressed')).toBeNull(); // dropped entirely -- trigger is menu-button-only now
    await click(name());
    expect(menu()).toBeTruthy();
    expect(onSelectContact).not.toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------
  // a11y: menu-button semantics + focus management
  // ---------------------------------------------------------------------

  it('the trigger announces ONE pattern -- menu-button (aria-haspopup/aria-expanded), never aria-pressed too (Pixel REVISE)', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful' },
    ]);
    expect(name().getAttribute('aria-haspopup')).toBe('menu');
    expect(name().getAttribute('aria-expanded')).toBe('false');
    expect(name().getAttribute('aria-pressed')).toBeNull();
    await click(name());
    expect(name().getAttribute('aria-expanded')).toBe('true');
    expect(name().getAttribute('aria-pressed')).toBeNull();
  });

  it('opening the menu focuses its first item; Escape closes it and returns focus to the trigger', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Dredge', reputation_tier: 'Outlaw' },
    ]);
    await click(name());
    expect(document.activeElement).toBe(menu()?.querySelector('[role="menuitem"]'));

    await keydown('Escape');
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(name());
  });

  it('the empty-state carries a status role', async () => {
    await mount([]);
    expect(container.querySelector('.empty-state')?.getAttribute('role')).toBe('status');
  });
});
