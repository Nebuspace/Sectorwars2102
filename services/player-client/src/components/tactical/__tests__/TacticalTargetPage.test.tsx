// @vitest-environment jsdom
/**
 * TacticalTargetPage — TACTICAL monitor's TARGET page (WO-UI2-DECK-
 * RECONCILE, §05, rewired by WO-TACTICAL-POPUP: rep-colored contacts,
 * clicking the NAME opens ContactActionMenu with whichever of ENGAGE/
 * APPROACH/HAIL apply, HAIL opens HailComposeDialog, reticle-select stays
 * decoupled; WO-TACTICAL-APPROACH-ENGAGE-SCROLL Part B: ENGAGE/APPROACH are
 * now split by proximity via WindshieldFlightContext, not rep bucket).
 *
 * Mirrors DeckPageTabs.test.tsx's harness (jsdom + react-dom/client
 * createRoot + act(), no RTL) — the SAME harness this file already used
 * pre-WO-TACTICAL-POPUP. ContactActionMenu/HailComposeDialog both portal
 * to document.body (ConfirmDialog's own idiom), so their content is
 * queried off `document.body`, not `container`, even though the row that
 * opened them lives inside `container`.
 *
 * Part B pulls in a REAL WindshieldFlightProvider (TacticalTargetPage now
 * calls useWindshieldFlight() directly) -- mirrors WindshieldTableau.
 * test.tsx's own harness for the same context. TacticalTargetPage.tsx also
 * now imports distancePx/REFERENCE_BAND/ENGAGE_RANGE_EM from
 * ../WindshieldTableau (the shared, not-duplicated range read), which
 * transitively pulls in that module's own apiClient/AutopilotContext
 * imports -- mocked below the SAME way WindshieldTableau.test.tsx already
 * mocks them (neither is ever actually CALLED here since <WindshieldTableau>
 * itself is never mounted in this file, only its named exports are used).
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

// Transitive-only (see file header): TacticalTargetPage.tsx now imports
// pure geometry helpers from ../WindshieldTableau, which itself imports
// apiClient + AutopilotContext at module scope -- mocked so those two real
// modules' own top-level code never executes, matching WindshieldTableau.
// test.tsx's own precedent.
vi.mock('../../../services/apiClient', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

let autopilotState: { status: string; abort: ReturnType<typeof vi.fn> };
vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => autopilotState,
}));

import TacticalTargetPage, { type TacticalContact } from '../pages/TacticalTargetPage';
import { WindshieldFlightProvider, useWindshieldFlight } from '../../../contexts/WindshieldFlightContext';

// Reference-band-relative near/far fixtures (WindshieldTableau.REFERENCE_BAND
// = 1440x334.7px @18.09px/em, ENGAGE_RANGE_EM = DOCK_RANGE_EM*3 = 15em ~=
// 271px) -- NEAR is a trivial 0.01%-xPct nudge (~0.14px), FAR is a 45%-xPct
// span (~648px), both comfortably on the correct side of the threshold
// regardless of the exact placeholder multiplier.
const SHIP_POS = { xPct: 50, yPct: 50 };
const NEAR_CONTACT_POS = { xPct: 50.01, yPct: 50 };
const FAR_CONTACT_POS = { xPct: 95, yPct: 50 };

describe('TacticalTargetPage', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  // Captures the shared flight context alongside the real TacticalTargetPage
  // mount, so a test can seed shipPos/contactPositions the same way
  // WindshieldTableau would publish them, and read pendingApproach back out
  // after an APPROACH click (mirrors WindshieldTableau.test.tsx's own
  // flightCapture harness).
  let flightCapture: ReturnType<typeof useWindshieldFlight> | null = null;
  function FlightCapture() {
    flightCapture = useWindshieldFlight();
    return null;
  }

  beforeEach(() => {
    localStorage.clear();
    mockEngage.mockReset();
    mockGetStatus.mockReset();
    mockSendPlayerMessage.mockReset();
    mockRefreshPlayerState.mockReset();
    autopilotState = { status: 'idle', abort: vi.fn() };
    flightCapture = null;
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
    flightCapture = null;
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <FlightCapture />
          <TacticalTargetPage contacts={contacts} onSelectContact={onSelectContact} selectedShipId={selectedShipId} />
        </WindshieldFlightProvider>
      );
    });
    await flush();
  };

  // Seeds the shared flight context the same way a mounted WindshieldTableau
  // would publish it -- `near` puts `shipId` inside ENGAGE_RANGE_EM of the
  // player's own ship, `far` puts it well outside.
  const seedRange = async (shipId: string, near: boolean) => {
    await act(async () => {
      flightCapture!.reportShipPos(SHIP_POS);
      flightCapture!.reportContactPositions(new Map([[shipId, near ? NEAR_CONTACT_POS : FAR_CONTACT_POS]]));
    });
    await flush();
  };

  const row = (idx = 0) => container.querySelectorAll('.target-contact-row')[idx] as HTMLElement;
  const name = (idx = 0) => row(idx).querySelector('.target-contact-name') as HTMLElement;
  // ContactActionMenu/HailComposeDialog both portal to document.body.
  const menu = () => document.body.querySelector('.contact-action-menu');
  const menuItem = (variant: 'engage' | 'hail' | 'approach') =>
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
    // Both carry a ship_id: whichever of ENGAGE/APPROACH applies -> both
    // are interactive triggers regardless of rep bucket (Part B: ENGAGE is
    // proximity-only, not rep-gated, and APPROACH is universal for any
    // ship-bearing contact) -- role coverage of the "no ship_id" no-menu
    // case is its own dedicated test below.
    expect(name(0).getAttribute('role')).toBe('button');
    expect(name(1).getAttribute('role')).toBe('button');
  });

  it('an NPC with no archetype but notoriety >= 50 is also RED (mirrors CombatInterface fair-game threshold)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '4', username: 'Rough Trader', is_npc: true, notoriety: 60 },
    ]);
    expect(name().getAttribute('title')).toContain('WANTED');
  });

  // ---------------------------------------------------------------------
  // ContactActionMenu composition — canEngage/canApproach/canHail are
  // independent predicates (WO-TACTICAL-POPUP, extended by Part B), not an
  // either/or button row. ENGAGE/APPROACH are split by proximity
  // (flight.shipPos/contactPositions vs ENGAGE_RANGE_EM), NOT rep bucket —
  // seedRange() puts a contact in/out of range the same way a mounted
  // WindshieldTableau would publish it.
  // ---------------------------------------------------------------------

  it('menu shows HAIL + APPROACH (never ENGAGE) for a FAR non-NPC BLUE contact', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    await seedRange('1', false);
    await click(name());
    expect(menuItem('hail')).toBeTruthy();
    expect(menuItem('approach')).toBeTruthy();
    expect(menuItem('engage')).toBeNull();
  });

  it('menu shows HAIL + ENGAGE (APPROACH gone) once the SAME player comes into range — the swap flips on a distance change', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    await seedRange('1', false);
    await click(name());
    expect(menuItem('approach')).toBeTruthy();
    expect(menuItem('engage')).toBeNull();

    await seedRange('1', true);
    expect(menuItem('engage')).toBeTruthy();
    expect(menuItem('approach')).toBeNull();
    expect(menuItem('hail')).toBeTruthy(); // unaffected by the range swap
  });

  it('menu shows APPROACH only for a FAR NPC (never hailable — is_npc excludes HAIL)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '2', username: 'Merchant Vessel', is_npc: true, archetype: 'LAW_ENFORCEMENT' },
    ]);
    await seedRange('2', false);
    await click(name());
    expect(menuItem('approach')).toBeTruthy();
    expect(menuItem('engage')).toBeNull();
    expect(menuItem('hail')).toBeNull();
  });

  it('menu shows ENGAGE only for an IN-RANGE NPC with a ship (never hailable)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '2', username: 'Crimson Corsair', is_npc: true, archetype: 'HOSTILE_RAIDER' },
    ]);
    await seedRange('2', true);
    await click(name());
    expect(menuItem('engage')).toBeTruthy();
    expect(menuItem('approach')).toBeNull();
    expect(menuItem('hail')).toBeNull();
  });

  it('menu shows BOTH ENGAGE and HAIL for an IN-RANGE hostile PLAYER contact (the old inline row could only ever show one)', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);
    await seedRange('1', true);
    await click(name());
    expect(menuItem('engage')).toBeTruthy();
    expect(menuItem('hail')).toBeTruthy();
    expect(menu()?.querySelectorAll('.contact-action-menu-item').length).toBe(2);
  });

  it('ENGAGE on an IN-RANGE BLUE/clean contact carries the rep-cost tooltip (title + aria-label) — v1 is a warning, not a hard block', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
    ]);
    await seedRange('1', true);
    await click(name());
    const engageBtn = menuItem('engage')!;
    expect(engageBtn).toBeTruthy();
    const warning = 'Engaging a clean target flags you as an outlaw: -100 rep + 1h grey';
    expect(engageBtn.getAttribute('title')).toBe(warning);
    expect(engageBtn.getAttribute('aria-label')).toContain(warning);
  });

  it('ENGAGE on an IN-RANGE RED/hostile contact carries NO cost tooltip', async () => {
    await mount([
      { player_id: 'p1', ship_id: '1', username: 'Dredge', reputation_tier: 'Outlaw', personal_reputation: -300 },
    ]);
    await seedRange('1', true);
    await click(name());
    const engageBtn = menuItem('engage')!;
    expect(engageBtn.getAttribute('title')).toBeFalsy();
  });

  it('the menu opens for EVERY ship-bearing contact, incl. a CLEAR NPC (APPROACH-only, no rep gate on the trigger itself)', async () => {
    await mount([
      { player_id: 'npc1', ship_id: '2', username: 'Merchant Vessel', is_npc: true, archetype: 'LAW_ENFORCEMENT' },
    ]);
    // No seedRange() -- default/no position data reported yet still counts
    // as "not in range", which is exactly the FAR/APPROACH default.
    expect(name().getAttribute('role')).toBe('button');
    await click(name());
    expect(menuItem('approach')).toBeTruthy();
  });

  it('shows no menu for an NPC with no ship_id (unattackable, unhailable, nothing to approach)', async () => {
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
    expect(menuItem('approach')).toBeNull(); // no ship_id -- nothing to glide toward
  });

  it('APPROACH click calls flight.approach(ship_id) — resolves on the shared WindshieldFlightContext', async () => {
    await mount([
      { player_id: 'p1', ship_id: 'ship-9', username: 'Vega', reputation_tier: 'Lawful' },
    ]);
    await seedRange('ship-9', false);
    await click(name());
    await click(menuItem('approach')!);

    expect(menu()).toBeNull(); // menu closes the instant an item is chosen
    expect(flightCapture?.pendingApproach?.objectId).toBe('ship-9');
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
    await seedRange('42', true);
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
    await seedRange('42', true);
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

  // Regression (mack HIGH, WO-TACTICAL-APPROACH-ENGAGE-SCROLL Part B REVISE):
  // a mid-menu range flip (APPROACH -> ENGAGE) changes the focused item's
  // React `key`, unmounting the focused button and mounting a new one at
  // that DOM position -- ContactActionMenu's initial-focus effect must
  // re-fire on that item-set change (not just on anchorEl) or focus drops
  // silently to document.body and the keyboard user's next Enter/Space is
  // inert until they mouse-click. mack's probe reproduced the drop; this
  // pins the fix permanently.
  it('a mid-menu range flip (Approach -> Engage swap) re-focuses the NEW first item, never drops to document.body', async () => {
    // An NPC (canHail is always false for is_npc) so APPROACH/ENGAGE is the
    // menu's ONLY item -- the swapped item IS the first item, making this
    // assert the actual mack-HIGH scenario unambiguously (a HAIL-bearing
    // player contact would leave HAIL, unaffected by the swap, sitting
    // first -- a real but weaker proof that this fix doesn't rely on).
    await mount([
      { player_id: 'npc1', ship_id: '1', username: 'Merchant Vessel', is_npc: true, archetype: 'LAW_ENFORCEMENT' },
    ]);
    await seedRange('1', false); // FAR -- menu shows APPROACH only
    await click(name());
    const approachBtn = menuItem('approach');
    expect(approachBtn).toBeTruthy();
    expect(menuItem('hail')).toBeNull();

    // A keyboard user has focused the APPROACH item (the menu's only item).
    approachBtn!.focus();
    expect(document.activeElement).toBe(approachBtn);

    // Range flip while the menu stays open on the SAME anchor -- APPROACH's
    // key unmounts, ENGAGE mounts in its place.
    await seedRange('1', true);
    const engageBtn = menuItem('engage');
    expect(engageBtn).toBeTruthy();
    expect(menuItem('approach')).toBeNull();

    // Focus must land on the menu's new first item, never document.body.
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(menu()?.querySelector('[role="menuitem"]'));
    expect(document.activeElement).toBe(engageBtn);
  });

  it('the empty-state carries a status role', async () => {
    await mount([]);
    expect(container.querySelector('.empty-state')?.getAttribute('role')).toBe('status');
  });

  // WO-TACTICAL-POPUP hub browser-prove polish note: a bottom-of-list
  // contact's trigger sat near the viewport bottom -- the old positioning
  // always anchored the menu BELOW the trigger, then clamped `top` upward
  // to stay in-viewport, landing the menu overlapping its own trigger row
  // instead of opening in the natural (flipped-up) direction.
  it('flips the menu UP when the trigger anchor is near the viewport bottom, instead of clamping it into an overlap with its own row', async () => {
    const spy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: Element
    ) {
      if (this.classList?.contains('target-contact-name')) {
        // Anchor sits near the bottom of a (jsdom-default ~768px) viewport.
        return {
          width: 120, height: 20, top: 730, left: 40, right: 160, bottom: 750, x: 40, y: 730,
          toJSON: () => ({}),
        } as DOMRect;
      }
      if (this.classList?.contains('contact-action-menu')) {
        return {
          width: 140, height: 150, top: 0, left: 0, right: 140, bottom: 150, x: 0, y: 0,
          toJSON: () => ({}),
        } as DOMRect;
      }
      return { width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });

    try {
      await mount([
        { player_id: 'p1', ship_id: '1', username: 'Vega', reputation_tier: 'Lawful', personal_reputation: 40 },
      ]);
      await click(name());

      const menuEl = menu() as HTMLElement;
      expect(menuEl).toBeTruthy();
      const top = parseFloat(menuEl.style.top);
      // The menu (150px tall + the same 4px gap the below-anchor path
      // uses) must land fully ABOVE the anchor's own top (730) -- never
      // overlapping the trigger row it opened from.
      expect(top + 150 + 4).toBeLessThanOrEqual(730);
    } finally {
      spy.mockRestore();
    }
  });
});
