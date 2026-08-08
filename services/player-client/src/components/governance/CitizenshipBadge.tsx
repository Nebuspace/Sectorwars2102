import React, { useCallback, useEffect, useState } from 'react';
import { governanceAPI } from '../../services/api';
import './citizenship-badge.css';

interface MembershipStatus {
  region_id: string;
  is_member: boolean;
  membership_type: string | null;
  stored_membership_type: string | null;
  owns_colony_in_region: boolean;
  can_vote: boolean;
  voting_power: number;
  citizenship_source: string | null;
}

interface CitizenshipBadgeProps {
  /** The region the player is currently in (currentSector.region_id). */
  regionId?: string | null;
  /** Friendly region name, for the title/aria text. */
  regionName?: string | null;
}

/**
 * Player-facing citizenship status for the current region (WO-CF, PATH A).
 *
 * Surfaces whether the player is on this region's voter roll. Owning a colony in
 * the region grants voting-citizenship: the badge reads the live
 * GET /regions/{id}/membership/me, which reports a colony owner as a citizen
 * (citizenship_source = "colony") even before the membership row is upgraded.
 *
 * WO-WIRE-CLAIM-COLONY-CITIZENSHIP: when the player owns a colony here but is
 * not yet a stored citizen, a Claim button POSTs /citizenship/colony-claim.
 */
const CitizenshipBadge: React.FC<CitizenshipBadgeProps> = ({ regionId, regionName }) => {
  const [status, setStatus] = useState<MembershipStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [claiming, setClaiming] = useState(false);
  const [claimError, setClaimError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!regionId) {
      setStatus(null);
      return;
    }
    setLoading(true);
    try {
      const data = (await governanceAPI.getMyMembership(regionId)) as MembershipStatus;
      setStatus(data);
      setClaimError(null);
    } catch {
      // Region may have no governance surface (e.g. Central Nexus) — stay quiet
      // rather than render a broken badge.
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [regionId]);

  useEffect(() => {
    let cancelled = false;
    if (!regionId) {
      setStatus(null);
      return;
    }
    setLoading(true);
    governanceAPI
      .getMyMembership(regionId)
      .then((data: MembershipStatus) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [regionId]);

  const handleClaim = async () => {
    if (!regionId || claiming) return;
    setClaiming(true);
    setClaimError(null);
    try {
      await governanceAPI.claimColonyCitizenship(regionId);
      await refresh();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Claim failed';
      setClaimError(msg);
    } finally {
      setClaiming(false);
    }
  };

  if (!regionId || loading || !status) return null;

  const isCitizen = status.membership_type === 'citizen';
  const onRoll = status.can_vote;
  const viaColony = status.citizenship_source === 'colony';
  const canClaim = status.owns_colony_in_region && !isCitizen;

  let label: string;
  let cls: string;
  if (isCitizen && onRoll) {
    label = viaColony ? 'CITIZEN · COLONY' : 'CITIZEN';
    cls = 'citizenship-badge citizen';
  } else if (onRoll) {
    label = 'VOTER';
    cls = 'citizenship-badge voter';
  } else {
    label = 'VISITOR';
    cls = 'citizenship-badge visitor';
  }

  const title = onRoll
    ? `You are on the voter roll in ${regionName || 'this region'}` +
      (viaColony ? ' — citizenship granted by owning a colony here.' : '.')
    : `You are not on the voter roll in ${regionName || 'this region'}. ` +
      'Own a colony here to gain voting-citizenship.';

  return (
    <div className={cls} title={title} aria-label={title} data-testid="citizenship-badge">
      <span className="citizenship-badge-icon">{onRoll ? '★' : '○'}</span>
      <span className="citizenship-badge-label">{label}</span>
      {canClaim && (
        <button
          type="button"
          className="citizenship-badge-claim"
          data-testid="citizenship-claim"
          disabled={claiming}
          onClick={handleClaim}
          title="Claim regional citizenship from colony ownership"
        >
          {claiming ? 'Claiming…' : 'Claim'}
        </button>
      )}
      {claimError && (
        <span className="citizenship-badge-error" role="status" data-testid="citizenship-claim-error">
          {claimError}
        </span>
      )}
    </div>
  );
};

export default CitizenshipBadge;
