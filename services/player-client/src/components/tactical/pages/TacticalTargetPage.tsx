import React, { useRef, useState } from 'react';
import { useGame } from '../../../contexts/GameContext';
import { combatAPI } from '../../../services/api';
import { InputValidator, SecurityAudit } from '../../../utils/security/inputValidation';
import { formatCredits } from '../../../utils/formatters';
import ContactActionMenu, { type ContactActionMenuItem } from '../ContactActionMenu';
import HailComposeDialog from '../HailComposeDialog';

/**
 * TacticalTargetPage — TACTICAL monitor's TARGET page (WO-UI2-DECK-
 * RECONCILE, §05: TACTICAL [TARGET · THREAT]).
 *
 * Relocated + enhanced from CommsMailbox's CONTACTS list (GameDashboard's
 * `sectorContacts` merge — live WS presence ∪ the players_present snapshot
 * — is passed down unchanged, same prop shape CommsMailbox received).
 * CommsMailbox.tsx itself is DELETED (WO-UI5-RETIREMENT+GLASS — zero
 * remaining consumers) — this was always a fresh implementation, not an
 * import, per §05's TARGET grammar: rep-colored names, context-aware
 * ENGAGE/HAIL, hover-record, name-click→reticle.
 *
 * WO-TACTICAL-POPUP: a row no longer shows its NPC/rep-bucket badges or
 * ENGAGE/HAIL as inline buttons — clicking the contact's NAME opens
 * ContactActionMenu (a small anchored a11y popup, ../ContactActionMenu)
 * listing whichever of ENGAGE/HAIL apply, replacing the old either/or
 * button-row (canEngage and canHail are independent predicates — a
 * hostile PLAYER contact can satisfy both at once, so the menu can show
 * both items together, something the old inline row's `canEngage ? … :
 * canHail ? … : null` couldn't). Trigger-gating is `canEngage ||
 * canHail`, NOT the old ship_id-only `selectable` -- that old gate
 * silently stranded hail-only contacts (canHail never required ship_id)
 * with no clickable trigger at all.
 *
 * a11y (Pixel REVISE, resolved): the name span is announced as ONE
 * pattern -- a MENU BUTTON (`aria-haspopup="menu"` + `aria-expanded`),
 * never both a menu-button AND a toggle-button. `aria-pressed` was
 * dropped from the trigger for exactly this reason -- an earlier pass
 * carried both attributes on the same element, which is two conflicting
 * ARIA button patterns on one node. Reticle-select (`onSelectContact`,
 * cross-boundary sync into the windshield's spotlight) still fires as a
 * real, wired side-effect of opening/closing the menu (the
 * `openMenuKey !== key` coupling below, unchanged) — it's just no longer
 * independently ANNOUNCED as a pressed/toggled state; the ◎ selected-badge
 * (visual) and the actual spotlight change in the viewport (functional)
 * carry that meaning instead of a second, conflicting ARIA role. Still
 * gated on ship_id (a reticle needs a ship to highlight) and still fires
 * off the same click as the menu-open when both apply. "Approach" (fly
 * toward the contact) is NOT in this menu: verify-first found no existing
 * flight primitive resolves a moving contact to a position
 * (WindshieldFlightContext.approach() only matches
 * system.bodies/system.stations) — spun out as WO-TACTICAL-APPROACH,
 * pending a pursuit-vs-snapshot design ruling.
 *
 * REP-COLOR mapping: §05's demo prose ("red is dead · gray struck the
 * lawful · blue in good standing") is a 3-bucket narrative device: the
 * REAL data model is the 8-tier personal_reputation scale (Villain..
 * Legendary, personal_reputation_service.py) already flowing through
 * player.reputation_tier/name_color. Rather than reinvent a parallel
 * red/gray/blue field server-side, this buckets the EXISTING tier string
 * into the same 3 semantic groups: Villain/Criminal/Outlaw -> WANTED
 * (red), Suspicious -> GREY-FLAG (gray), Neutral/Lawful/Heroic/Legendary
 * -> CLEAR (blue). NPCs have no personal_reputation at all (only
 * archetype/notoriety) -- hostFn below mirrors CombatInterface.tsx's own
 * npcStanding() "fair game" threshold (archetype===HOSTILE_RAIDER or
 * notoriety>=50) so a Corsair-type NPC reads the same "attackable" way in
 * both surfaces, without a shared import (CombatInterface.tsx is a
 * different feature/route, not owned by this WO).
 *
 * 🔴 A11Y: the rep bucket is no longer a permanent visible text tag on the
 * row (WO-TACTICAL-POPUP removed it with the button row) — it survives in
 * the name's `title` hover tooltip (`record`, built below) and, more
 * importantly, in the SAME color already carrying rep meaning being paired
 * with the always-visible menu items' own text labels once opened
 * (ENGAGE only ever appears for a red/attackable contact). Flagged for
 * Pixel: a hover-only tooltip alone would fail WCAG 1.4.1 the same way
 * the retired threat-band chips did; the menu items' own text is the
 * channel that keeps this from being color-alone in practice.
 *
 * ENGAGE reuses the shipped combat pipeline (combatAPI.engage/getStatus,
 * the same calls CombatInterface.tsx makes) with the same InputValidator
 * rate-limit/param-validation guard that path already has — a new attack
 * surface must not be weaker than the existing one. The full round-by-
 * round replay ("arena when engaged") is CombatInterface's job and stays
 * out of this compact monitor; ENGAGE here shows the resolved headline
 * only (VICTORY/DEFEATED/etc, mirrors CombatInterface's own
 * getResultHeadline mapping).
 *
 * HAIL opens HailComposeDialog (../HailComposeDialog, own local state, NOT
 * CommsMailbox's mailbox UI) using useGame().sendPlayerMessage directly —
 * the full inbox/reply mailbox lives at MFD-B COMM (mfd-lane, out of scope
 * here).
 */

export interface TacticalContact {
  player_id?: string;
  user_id?: string;
  id?: string;
  ship_id?: string;
  username?: string;
  name?: string;
  is_npc?: boolean;
  name_color?: string;
  military_rank?: string;
  reputation_tier?: string;
  personal_reputation?: number;
  /** NPC-only enrichment (npc_spawn_service._presence_entry / player.py). */
  archetype?: string;
  notoriety?: number;
}

interface TacticalTargetPageProps {
  contacts: TacticalContact[];
  selectedShipId?: string | null;
  onSelectContact?: (contact: TacticalContact | null) => void;
}

type RepBucket = 'red' | 'gray' | 'blue';

const BUCKET_COLOR: Record<RepBucket, string> = {
  red: '#FF5A6A',
  gray: '#9AA6B5',
  blue: '#5FB8FF',
};

// Exact vocabulary requested for the a11y text-tag (never color alone).
const BUCKET_TAG: Record<RepBucket, string> = {
  red: 'WANTED',
  gray: 'GREY-FLAG',
  blue: 'CLEAR',
};

const RED_TIERS = new Set(['Villain', 'Criminal', 'Outlaw']);
const GRAY_TIERS = new Set(['Suspicious']);

const playerRepBucket = (tier?: string): RepBucket => {
  if (tier && RED_TIERS.has(tier)) return 'red';
  if (tier && GRAY_TIERS.has(tier)) return 'gray';
  return 'blue';
};

// Mirrors CombatInterface.tsx's npcStanding() "fair game" threshold —
// same archetype/notoriety read, kept in sync by convention (see file
// header) rather than a shared import.
const isHostileNpc = (contact: TacticalContact): boolean => {
  const arch = String(contact.archetype || '').toUpperCase();
  if (arch === 'HOSTILE_RAIDER') return true;
  if (arch === 'LAW_ENFORCEMENT') return false;
  return typeof contact.notoriety === 'number' && contact.notoriety >= 50;
};

const repBucket = (contact: TacticalContact): RepBucket =>
  contact.is_npc ? (isHostileNpc(contact) ? 'red' : 'blue') : playerRepBucket(contact.reputation_tier);

const contactDisplayName = (contact: TacticalContact): string =>
  contact.username || contact.name || 'UNKNOWN CONTACT';

const contactKey = (contact: TacticalContact): string =>
  (contact.is_npc && contact.player_id) || contact.user_id || contact.id || contact.username || '';

// Mirrors CombatInterface.tsx's getResultHeadline exactly (same combat
// payload shape) so the SAME resolved fight reads identically wherever a
// player sees it.
const resultHeadline = (status: any, selfId?: string): string => {
  if (status.outcome === 'escaped') return 'DISENGAGED';
  if (status.winner === 'draw') {
    const destroyers = new Set(
      (status.rounds || [])
        .filter((e: any) => e.action === 'ship_destroyed' && e.actor)
        .map((e: any) => e.actor)
    );
    if (destroyers.has('attacker') && destroyers.has('defender')) return 'MUTUAL DESTRUCTION';
    return 'STALEMATE';
  }
  if (status.winner && selfId && status.winner === selfId) {
    return status.creditsLooted ? `VICTORY — ${formatCredits(status.creditsLooted)} looted` : 'VICTORY';
  }
  return 'DEFEATED';
};

const TacticalTargetPage: React.FC<TacticalTargetPageProps> = ({ contacts, selectedShipId, onSelectContact }) => {
  const { playerState, refreshPlayerState, sendPlayerMessage } = useGame();

  const [engagingKey, setEngagingKey] = useState<string | null>(null);
  const [engageResult, setEngageResult] = useState<{ key: string; ok: boolean; text: string } | null>(null);

  const [hailKey, setHailKey] = useState<string | null>(null);
  const [hailText, setHailText] = useState('');
  const [hailBusy, setHailBusy] = useState(false);
  const [hailResult, setHailResult] = useState<{ key: string; ok: boolean; text: string } | null>(null);

  // WO-TACTICAL-POPUP: which row's ContactActionMenu is open (one at a
  // time). `triggerRefs` holds each row's name-span DOM node, keyed the
  // same as `contactKey()` -- every row's span attaches unconditionally on
  // mount (React's ref callback fires well before any menu-open click), so
  // the entry is always already populated by the time a click reads it.
  const [openMenuKey, setOpenMenuKey] = useState<string | null>(null);
  const triggerRefs = useRef<Map<string, HTMLElement>>(new Map());

  const startHail = (key: string) => {
    setHailKey(key);
    setHailText('');
    setHailResult(null);
  };

  const cancelHail = () => {
    setHailKey(null);
    setHailText('');
  };

  const sendHail = async (contact: TacticalContact, key: string) => {
    if (!contact.player_id || !hailText.trim() || hailBusy) return;
    setHailBusy(true);
    try {
      await sendPlayerMessage(contact.player_id, hailText.trim(), null, null);
      setHailResult({ key, ok: true, text: 'TRANSMITTED' });
      setHailKey(null);
      setHailText('');
    } catch (e: any) {
      setHailResult({ key, ok: false, text: e?.message || 'TRANSMISSION FAILED' });
    } finally {
      setHailBusy(false);
    }
  };

  const handleEngage = async (contact: TacticalContact, key: string) => {
    if (!playerState || engagingKey || !contact.ship_id) return;

    const validation = InputValidator.validateCombatParams({
      targetType: 'ship',
      targetId: contact.ship_id,
    });
    if (!validation.valid) {
      setEngageResult({ key, ok: false, text: validation.errors.join(', ') });
      return;
    }
    if (!InputValidator.checkRateLimit(`combat_${playerState.id}`, 5, 60000)) {
      SecurityAudit.log({
        type: 'rate_limit_exceeded',
        details: { action: 'combat_initiation' },
        userId: playerState.id,
      });
      setEngageResult({ key, ok: false, text: 'Too many combat attempts — wait before engaging again.' });
      return;
    }

    setEngagingKey(key);
    setEngageResult(null);
    try {
      const response = await combatAPI.engage('ship', contact.ship_id);
      if (response.status === 'initiated' && response.combatId) {
        const status = await combatAPI.getStatus(response.combatId);
        if (status?.status === 'completed') {
          InputValidator.clearRateLimit(`combat_${playerState.id}`);
          refreshPlayerState();
          setEngageResult({
            key,
            ok: !!status.winner && status.winner === playerState.id,
            text: resultHeadline(status, playerState.id),
          });
        }
      } else {
        setEngageResult({ key, ok: false, text: response?.message || 'Failed to initiate combat' });
      }
    } catch (e: any) {
      setEngageResult({ key, ok: false, text: e?.message || 'Combat system error — try again.' });
    } finally {
      setEngagingKey(null);
    }
  };

  if (contacts.length === 0) {
    return (
      <div className="empty-state" role="status">
        No contacts in sector
      </div>
    );
  }

  return (
    <div className="target-contact-list" role="list" aria-label="Sector contacts">
      {contacts.map((contact) => {
        const key = contactKey(contact);
        const bucket = repBucket(contact);
        const color = contact.is_npc ? BUCKET_COLOR[bucket] : contact.name_color || BUCKET_COLOR[bucket];
        const tag = BUCKET_TAG[bucket];
        const canEngage = bucket === 'red' && !!contact.ship_id;
        const canHail = !contact.is_npc && !!contact.player_id;
        // Trigger-gating fix (WO-TACTICAL-POPUP, see file header): the
        // menu opens whenever it has anything to show, not merely when a
        // ship_id exists.
        const menuHasItems = canEngage || canHail;
        // Reticle-select stays its own, ship_id-gated concern -- separate
        // from whether the menu has anything to offer.
        const canSelect = !!onSelectContact && !!contact.ship_id;
        const selected = !!contact.ship_id && String(contact.ship_id) === String(selectedShipId ?? '');
        const menuOpen = openMenuKey === key;
        const engaging = engagingKey === key;
        const composing = hailKey === key;
        const record = contact.is_npc
          ? `${tag} · ${contact.archetype ? contact.archetype.replace(/_/g, ' ').toLowerCase() : 'unknown craft'}`
          : `${tag} · ${contact.reputation_tier || 'Neutral'} (${(contact.personal_reputation ?? 0) >= 0 ? '+' : ''}${contact.personal_reputation ?? 0})`;

        const closeMenu = () => setOpenMenuKey((cur) => (cur === key ? null : cur));
        const activateTrigger = () => {
          if (!menuHasItems) return;
          // Both axes move together off ONE source of truth -- whether
          // THIS click is opening or closing the menu -- rather than off
          // `selected`, which can already be true from an external
          // reticle-select (SolarSystemViewscreen) with this menu still
          // closed; keying the select-toggle off `selected` in that case
          // would deselect on the very click that opens the menu.
          const opening = openMenuKey !== key;
          setOpenMenuKey(opening ? key : null);
          if (canSelect) onSelectContact!(opening ? contact : null);
        };

        const menuItems: ContactActionMenuItem[] = [];
        if (canEngage) {
          menuItems.push({
            key: 'engage',
            label: engaging ? '…' : 'ENGAGE ▸',
            variant: 'engage',
            onSelect: () => {
              closeMenu();
              handleEngage(contact, key);
            },
          });
        }
        if (canHail) {
          menuItems.push({
            key: 'hail',
            label: 'HAIL',
            variant: 'hail',
            onSelect: () => {
              closeMenu();
              startHail(key);
            },
          });
        }

        return (
          <div key={key} className="target-contact-row" role="listitem">
            <div className="target-contact-main">
              <span className="status-indicator online" aria-hidden="true"></span>
              <span
                ref={(el) => {
                  if (el) triggerRefs.current.set(key, el);
                  else triggerRefs.current.delete(key);
                }}
                className="target-contact-name"
                style={{ color }}
                role={menuHasItems ? 'button' : undefined}
                tabIndex={menuHasItems ? 0 : undefined}
                aria-haspopup={menuHasItems ? 'menu' : undefined}
                aria-expanded={menuHasItems ? menuOpen : undefined}
                title={record}
                onClick={menuHasItems ? activateTrigger : undefined}
                onKeyDown={
                  menuHasItems
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          activateTrigger();
                        }
                      }
                    : undefined
                }
              >
                {contact.military_rank ? `${contact.military_rank.toUpperCase()} ` : ''}
                {contactDisplayName(contact)}
                {selected && <span className="target-selected-badge" aria-hidden="true"> ◎</span>}
              </span>
            </div>

            {menuOpen && menuItems.length > 0 && (
              <ContactActionMenu
                anchorEl={triggerRefs.current.get(key) ?? null}
                items={menuItems}
                label={`Actions for ${contactDisplayName(contact)}`}
                onClose={closeMenu}
              />
            )}

            {composing && (
              <HailComposeDialog
                contactName={contactDisplayName(contact)}
                value={hailText}
                onChange={setHailText}
                onSend={() => sendHail(contact, key)}
                onCancel={cancelHail}
                busy={hailBusy}
                error={hailResult?.key === key && !hailResult.ok ? hailResult.text : null}
              />
            )}

            {/* The menu closes the instant ENGAGE is chosen (standard menu
                exit-on-select) -- this row-level status is the only
                feedback left for the in-flight combat call, filling the
                gap the old inline button's own disabled/"…" state used to
                cover. */}
            {engaging && (
              <div className="target-result-msg" role="status" aria-live="polite">
                ENGAGING…
              </div>
            )}
            {engageResult?.key === key && (
              <div className={`target-result-msg ${engageResult.ok ? 'ok' : 'err'}`} role="status" aria-live="polite">
                {engageResult.text}
              </div>
            )}
            {/* Composing shows its own (possible) error inside the dialog
                above -- once composing ends (sent, or cancelled) any
                hailResult surfaces here same as before. */}
            {hailResult?.key === key && !composing && (
              <div className={`target-result-msg ${hailResult.ok ? 'ok' : 'err'}`} role="status" aria-live="polite">
                {hailResult.text}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default TacticalTargetPage;
