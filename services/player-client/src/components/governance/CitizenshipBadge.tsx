import React, { useEffect, useState } from 'react';
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
 */
const CitizenshipBadge: React.FC<CitizenshipBadgeProps> = ({ regionId, regionName }) => {
  const [status, setStatus] = useState<MembershipStatus | null>(null);
  const [loading, setLoading] = useState(false);

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
        // Region may have no governance surface (e.g. Central Nexus) — stay quiet
        // rather than render a broken badge.
        if (!cancelled) setStatus(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [regionId]);

  if (!regionId || loading || !status) return null;

  const isCitizen = status.membership_type === 'citizen';
  const onRoll = status.can_vote;
  const viaColony = status.citizenship_source === 'colony';

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
    <div className={cls} title={title} aria-label={title}>
      <span className="citizenship-badge-icon">{onRoll ? '★' : '○'}</span>
      <span className="citizenship-badge-label">{label}</span>
    </div>
  );
};

export default CitizenshipBadge;
