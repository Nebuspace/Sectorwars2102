/**
 * Trade Contract types — mirrors gameserver/src/api/routes/contracts.py's
 * `_serialize_contract` shape and each write-action's response shape
 * exactly (contracts.py:30-52, :266-274, :356-362, :413-418, :550-558,
 * :617-622). See SYSTEMS/contracts.md for the canonical contract
 * lifecycle and the escrow table.
 */

export type ContractIssuerType = 'npc' | 'player';

export type ContractType =
  | 'cargo_delivery'
  | 'bulk_procurement'
  | 'express_delivery'
  | 'hazardous_transport'
  | 'refugee_transport'
  | 'acquisition_bounty'
  | 'escort';

export type ContractStatus =
  | 'posted'
  | 'accepted'
  | 'in_progress'
  | 'partial_fulfilled'
  | 'completed'
  | 'cancelled'
  | 'disputed'
  | 'expired';

export type ContractEscrowState = 'held' | 'released' | 'disputed' | 'refunding';

/** Raw wire shape returned by GET /board, GET /mine, GET /{id}. */
export interface ContractDTO {
  id: string;
  issuer_type: ContractIssuerType;
  issuer_id: string;
  acceptor_player_id: string | null;
  contract_type: ContractType;
  status: ContractStatus;
  origin_station_id: string | null;
  destination_station_id: string;
  commodity_type: string;
  quantity: number;
  payment: number | null;
  penalty: number | null;
  acceptance_fee_pct: number | null;
  escrow_amount: number | null;
  escrow_state: ContractEscrowState | null;
  faction_id: string | null;
  deadline: string | null;
  posted_at: string | null;
  accepted_at: string | null;
  completed_at: string | null;
}

export interface ContractMineResponse {
  posted: ContractDTO[];
  accepted: ContractDTO[];
}

export interface ContractAcceptResponse {
  id: string;
  status: ContractStatus;
  acceptor_player_id: string;
  accepted_at: string;
  acceptance_fee_charged: number;
  remaining_balance: number;
  deadline: string | null;
}

export interface ContractCompleteResponse {
  id: string;
  status: ContractStatus;
  completed_at: string;
  payout: number;
  credits: number;
}

export interface ContractAbandonResponse {
  id: string;
  status: ContractStatus;
  penalty_charged: number;
  credits: number;
}

export interface ContractPostResponse {
  id: string;
  status: ContractStatus;
  escrow_amount: number;
  escrow_state: ContractEscrowState;
  posted_at: string;
  acceptance_fee_pct: number;
  credits: number;
}

export interface ContractCancelResponse {
  id: string;
  status: ContractStatus;
  refund: number;
  credits: number;
}

/** POST /contracts request body — PostContractRequest (contracts.py:62-73).
 * cargo_delivery only this stage — there is no contract_type field, it is
 * implicit. `deadline` is an ISO-8601 datetime string. */
export interface PostContractRequest {
  destination_station_id: string;
  commodity_type: string;
  quantity: number;
  payment: number;
  deadline: string;
  origin_station_id?: string;
  insurance_pool_reserve?: number;
}
