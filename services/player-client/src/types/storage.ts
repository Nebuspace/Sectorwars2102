/**
 * Storage locker types — mirrors gameserver/src/api/routes/storage.py and
 * src/services/storage_service.py (FEATURES/economy/storage-lockers.md).
 * Unlike contract.ts's snake_case ContractDTO, the locker list/serialize
 * shape is camelCase (storage.py's own `_serialize_locker` convention);
 * the per-action responses (deposit/retrieve) stay snake_case, matching
 * storage_service's own return dicts. Callers type-cast at the call site
 * (same convention as contract.ts / api.ts's contractsAPI).
 */

/** One row of GET /api/v1/storage/lockers/claimable — a CLAIMABLE locker
 * (a multi-trip contract that missed its deadline before reaching full
 * quantity) whose deposited cargo the owner can still retrieve. */
export interface ClaimableLockerDTO {
  id: string;
  status: string;
  stationId: string;
  commodity: string;
  storedUnits: number;
  /** Rent owed as of the last settlement, NOT a live/exact figure — rent
   * keeps accruing between settlements (storage_service.settle_fee). */
  accruedFee: number;
  rentRate: number;
  createdAt: string | null;
}

/** POST /api/v1/storage/lockers/{id}/retrieve response
 * (storage_service.retrieve_claimable_cargo). Retrieval settles rent up
 * to the call, THEN moves cargo — `remaining > 0` means the ship couldn't
 * hold it all in one trip and the locker stays CLAIMABLE for a return trip. */
export interface RetrieveCargoResponse {
  locker_id: string;
  retrieved: number;
  commodity: string | null;
  remaining: number;
  released: boolean;
  fee_charged: number;
}
