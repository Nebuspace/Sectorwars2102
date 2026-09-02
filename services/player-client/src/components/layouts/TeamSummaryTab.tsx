import React, { useEffect, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { teamAPI } from '../../services/api';
import type { Team, TeamApiResponse, TeamPermissionsApiResponse } from '../../types/team';
import EmptyState from '../common/EmptyState';
import { TeamWarPanel } from '../teams/TeamWarPanel';

/**
 * TeamSummaryTab — the StatusBar dossier dropdown's "Crew" tab
 * (WO-UI5-DOSSIER sub-part #1).
 *
 * TeamManager.tsx (components/teams/) is the full CREW MANIFEST console
 * — far too heavy to embed in this fixed-size dropdown. Live crew surface
 * today: identity/rating/role plus LEG-73 TeamWarPanel (compact) so
 * declare/list/ceasefire is reachable without remounting TeamManager.
 */

const RECRUITMENT_TO_UI: Record<string, Team['recruitmentStatus']> = {
  OPEN: 'open',
  INVITE_ONLY: 'invite-only',
  CLOSED: 'closed',
};

const mapTeam = (raw: TeamApiResponse): Team => ({
  id: raw.id,
  name: raw.name,
  tag: raw.tag ?? '',
  description: raw.description ?? '',
  leaderId: raw.leader_id,
  memberCount: raw.member_count,
  maxMembers: raw.max_members,
  founded: raw.created_at,
  recruitmentStatus: RECRUITMENT_TO_UI[raw.recruitment_status] ?? 'closed',
  combatRating: raw.combat_rating,
  tradeRating: raw.trade_rating,
  totalPlanets: raw.total_planets,
  treasury: {
    credits: raw.treasury_credits,
  },
});

const formatRole = (role: string | null): string => {
  if (!role) return '—';
  return role.charAt(0) + role.slice(1).toLowerCase();
};

const RECRUITMENT_LABEL: Record<Team['recruitmentStatus'], string> = {
  open: 'Open',
  'invite-only': 'Invite Only',
  closed: 'Closed',
};

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

/** True when err looks like gameserver detail (not bare API Error: N / TypeError noise). */
function hasTeamSummaryServerDetail(err: unknown, message: string | undefined): boolean {
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy — use the caller fallback.
  if (err instanceof TypeError) return false;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** apiRequest throws Error with `.status`; surface gameserver 403 detail on crew load. */
export function formatTeamSummaryLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasTeamSummaryServerDetail(err, message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You are not a member of this team.';
  }

  if (status === 429) {
    return 'Team load rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load team data';
}

const TeamSummaryTab: React.FC = () => {
  const { playerState } = useGame();
  const teamId = playerState?.team_id ?? null;

  const [team, setTeam] = useState<Team | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!teamId);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!teamId) {
      setTeam(null);
      setRole(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      teamAPI.getTeam(teamId) as Promise<TeamApiResponse>,
      teamAPI.getPermissions(teamId) as Promise<TeamPermissionsApiResponse>,
    ])
      .then(([teamData, permData]) => {
        if (cancelled) return;
        setTeam(mapTeam(teamData));
        setRole(permData.role);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(formatTeamSummaryLoadError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId]);

  if (error) {
    return <div className="sb-crew-error" role="alert">{error}</div>;
  }

  if (loading) {
    return (
      <div className="sb-crew-loading" role="status" aria-live="polite">
        Loading…
      </div>
    );
  }

  if (!team) {
    return (
      <EmptyState
        icon="👥"
        title="No Team"
        message="You are not currently a member of any team."
      />
    );
  }

  const isLeader = role === 'LEADER';

  return (
    <div className="sb-crew-summary">
      <h2 className="sb-crew-name">
        {team.tag ? `[${team.tag}] ` : ''}{team.name}
      </h2>
      <div className="sb-crew-grid">
        <div className="sb-identity-field">
          <span className="sb-identity-k">YOUR ROLE</span>
          <span className="sb-identity-v">{formatRole(role)}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">MEMBERS</span>
          <span className="sb-identity-v">{team.memberCount}/{team.maxMembers}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">PLANETS</span>
          <span className="sb-identity-v">{team.totalPlanets}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">RECRUITMENT</span>
          <span className="sb-identity-v">{RECRUITMENT_LABEL[team.recruitmentStatus]}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">COMBAT RATING</span>
          <span className="sb-identity-v">{team.combatRating.toFixed(1)}</span>
        </div>
        <div className="sb-identity-field">
          <span className="sb-identity-k">TRADE RATING</span>
          <span className="sb-identity-v">{team.tradeRating.toFixed(1)}</span>
        </div>
      </div>
      <TeamWarPanel teamId={team.id} isLeader={isLeader} compact />
    </div>
  );
};

export default TeamSummaryTab;
