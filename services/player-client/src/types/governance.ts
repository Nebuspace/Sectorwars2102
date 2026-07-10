// Shared types for the regional-governance member UI (WO-REGOV-VOTE-UI).
// Field names mirror the wire shapes returned by regional_governance.py's
// member-facing routes verbatim (snake_case) -- these are read-mostly views
// with no local UI model, so there is no camelCase remapping layer here
// (unlike types/team.ts).

export interface MembershipStatus {
  region_id: string;
  is_member: boolean;
  membership_type: string | null;
  stored_membership_type: string | null;
  owns_colony_in_region: boolean;
  can_vote: boolean;
  voting_power: number;
  citizenship_source: string | null;
}

// election.candidates JSONB entries: the server writes {player_id, platform?}
// (register_candidate) but a pre-existing election could also carry a bare
// id string (start_election's optional `candidates` seed) -- callers should
// treat a raw string as {player_id: <string>}.
export interface Candidate {
  player_id: string;
  platform?: string;
}

export type ElectionStatusValue = 'pending' | 'active' | 'completed' | 'cancelled';

export interface ElectionResults {
  tallies: Record<string, number>;
  total_weight: number;
  winner: string | null;
  voided: boolean;
  position: string;
  tallied_at: string;
  inconclusive?: boolean;
}

export interface Election {
  id: string;
  position: string;
  candidates: Array<Candidate | string>;
  voting_opens_at: string;
  voting_closes_at: string;
  results: ElectionResults | null;
  status: ElectionStatusValue;
}

export type PolicyStatusValue = 'voting' | 'passed' | 'rejected' | 'implemented';

export interface Policy {
  id: string;
  policy_type: string;
  title: string;
  description: string | null;
  proposed_changes: Record<string, unknown>;
  proposed_by: string;
  proposed_at: string;
  voting_closes_at: string;
  votes_for: number;
  votes_against: number;
  status: PolicyStatusValue;
  approval_percentage: number | null;
}

// Member-facing view -- `terms` is redacted server-side (only the owner's
// /my-region/treaties read includes it).
export interface Treaty {
  id: string;
  partner_region: string | null;
  treaty_type: string;
  signed_at: string;
  expires_at: string | null;
  status: string;
}

// The 6 keys policy_proposal_rules.validate_proposed_changes recognizes --
// any other top-level key in proposed_changes is rejected with a 400.
export const KNOWN_POLICY_KEYS = [
  'tax_rate',
  'voting_threshold',
  'election_frequency_days',
  'governance_type',
  'governance_quorum_pct',
  'trade_bonuses',
] as const;

export type KnownPolicyKey = typeof KNOWN_POLICY_KEYS[number];

// Canon CHECK-bound ranges (services/policy_proposal_rules.py) -- shown as
// client-side hints only; the server remains the authoritative validator.
export const POLICY_FIELD_RANGES: Record<string, { min: number; max: number }> = {
  tax_rate: { min: 0.05, max: 0.25 },
  voting_threshold: { min: 0.1, max: 0.9 },
  election_frequency_days: { min: 30, max: 365 },
  governance_quorum_pct: { min: 0.25, max: 0.6 },
};

export const GOVERNANCE_TYPES = ['autocracy', 'democracy', 'council'] as const;

// Illustrative example set from FEATURES/gameplay/regional-governance.md
// ("tax_rate" | "pvp_rules" | "trade_policy" | ...) -- policy_type has no
// server-enforced enum (free string on PolicyCreate), so this is a curated
// UI convenience, not a closed canon list.
export const POLICY_TYPE_SUGGESTIONS = [
  'tax_rate',
  'pvp_rules',
  'trade_policy',
  'governance_change',
] as const;
