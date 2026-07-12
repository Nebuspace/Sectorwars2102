import React, { useState } from 'react';
import { useGame } from '../../../contexts/GameContext';
import { combatAPI } from '../../../services/api';
import { InputValidator, SecurityAudit } from '../../../utils/security/inputValidation';
import { formatCredits } from '../../../utils/formatters';

/**
 * TacticalTargetPage — TACTICAL monitor's TARGET page (WO-UI2-DECK-
 * RECONCILE, §05: TACTICAL [TARGET · THREAT]).
 *
 * Relocated + enhanced from CommsMailbox's CONTACTS list (GameDashboard's
 * `sectorContacts` merge — live WS presence ∪ the players_present snapshot
 * — is passed down unchanged, same prop shape CommsMailbox received).
 * CommsMailbox.tsx itself is untouched (read-only reference; orphaned,
 * cleaned up by a later WO) — this is a fresh implementation, not an
 * import, per §05's TARGET grammar: rep-colored names, context-aware
 * ENGAGE/HAIL, hover-record, name-click→reticle.
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
 * 🔴 A11Y: color is reinforcement, never the sole channel (WCAG 1.4.1,
 * same lesson as the threat-band chips this WO retires) — every row
 * carries its rep bucket as a visible TEXT tag (WANTED/GREY-FLAG/CLEAR)
 * alongside the color, not just on hover.
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
 * HAIL is a compact inline composer (own local state, NOT CommsMailbox's
 * mailbox UI) using useGame().sendPlayerMessage directly — the full
 * inbox/reply mailbox lives at MFD-B COMM (mfd-lane, out of scope here).
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
        const selectable = !!onSelectContact && !!contact.ship_id;
        const selected = !!contact.ship_id && String(contact.ship_id) === String(selectedShipId ?? '');
        const canEngage = bucket === 'red' && !!contact.ship_id;
        const canHail = !contact.is_npc && !!contact.player_id;
        const engaging = engagingKey === key;
        const composing = hailKey === key;
        const record = contact.is_npc
          ? `${tag} · ${contact.archetype ? contact.archetype.replace(/_/g, ' ').toLowerCase() : 'unknown craft'}`
          : `${tag} · ${contact.reputation_tier || 'Neutral'} (${(contact.personal_reputation ?? 0) >= 0 ? '+' : ''}${contact.personal_reputation ?? 0})`;

        return (
          <div key={key} className="target-contact-row" role="listitem">
            <div className="target-contact-main">
              <span className="status-indicator online" aria-hidden="true"></span>
              <span
                className="target-contact-name"
                style={{ color }}
                role={selectable ? 'button' : undefined}
                tabIndex={selectable ? 0 : undefined}
                aria-pressed={selectable ? selected : undefined}
                title={record}
                onClick={selectable ? () => onSelectContact!(selected ? null : contact) : undefined}
                onKeyDown={
                  selectable
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          onSelectContact!(selected ? null : contact);
                        }
                      }
                    : undefined
                }
              >
                {contact.military_rank ? `${contact.military_rank.toUpperCase()} ` : ''}
                {contactDisplayName(contact)}
                {selected && <span className="target-selected-badge" aria-hidden="true"> ◎</span>}
              </span>
              {contact.is_npc && <span className="target-npc-badge">NPC</span>}
              <span className={`target-rep-tag target-rep-${bucket}`}>{tag}</span>
            </div>

            <div className="target-contact-actions">
              {canEngage ? (
                <button
                  type="button"
                  className="target-action-btn target-engage-btn"
                  onClick={() => handleEngage(contact, key)}
                  disabled={!!engagingKey}
                  aria-busy={engaging}
                >
                  {engaging ? '…' : 'ENGAGE ▸'}
                </button>
              ) : canHail ? (
                composing ? (
                  <div className="target-hail-compose">
                    <input
                      type="text"
                      className="target-hail-input"
                      value={hailText}
                      onChange={(e) => setHailText(e.target.value)}
                      placeholder="TRANSMISSION…"
                      maxLength={500}
                      disabled={hailBusy}
                      aria-label={`Hail message to ${contactDisplayName(contact)}`}
                    />
                    <button
                      type="button"
                      className="target-action-btn target-hail-send-btn"
                      onClick={() => sendHail(contact, key)}
                      disabled={hailBusy || !hailText.trim()}
                      aria-label={hailText.trim() ? 'Send message' : 'Send message (enter text first)'}
                      aria-busy={hailBusy}
                    >
                      {hailBusy ? '…' : 'SEND'}
                    </button>
                    <button
                      type="button"
                      className="target-hail-cancel-btn"
                      onClick={cancelHail}
                      disabled={hailBusy}
                      aria-label="Cancel hail"
                    >
                      ×
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="target-action-btn target-hail-btn"
                    onClick={() => startHail(key)}
                  >
                    HAIL
                  </button>
                )
              ) : null}
            </div>

            {engageResult?.key === key && (
              <div className={`target-result-msg ${engageResult.ok ? 'ok' : 'err'}`} role="status">
                {engageResult.text}
              </div>
            )}
            {hailResult?.key === key && (
              <div className={`target-result-msg ${hailResult.ok ? 'ok' : 'err'}`} role="status">
                {hailResult.text}
              </div>
            )}
          </div>
        );
      })}

      <div className="target-legend">
        <span className="target-rep-tag target-rep-red">WANTED</span> outlaw · dead to rights —{' '}
        <span className="target-rep-tag target-rep-gray">GREY-FLAG</span> struck the lawful —{' '}
        <span className="target-rep-tag target-rep-blue">CLEAR</span> good standing
      </div>
    </div>
  );
};

export default TacticalTargetPage;
