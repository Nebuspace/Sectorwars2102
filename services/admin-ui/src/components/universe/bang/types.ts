/**
 * TypeScript types mirroring the gameserver Pydantic schemas for the
 * sw2102-bang generation pipeline. Source of truth:
 *
 *   services/gameserver/src/schemas/bang_config.py
 *   services/gameserver/src/schemas/bang_job.py
 *
 * Keep this file in lock-step with those schemas; any field added on the
 * backend must be reflected here for typecheck to flow through the form.
 */

export type BangRegionType = 'player_owned' | 'terran_space' | 'central_nexus';

export type BangValidatorStrictness = 'lenient' | 'standard' | 'strict';

/** Mirrors `BangConfig` (bang_config.py). All optional fields default in bang. */
export interface BangConfig {
  // Required identity / region knobs (gameserver-driven)
  seed: number;
  sectors: number;
  region_type: BangRegionType;

  // Zone distribution (must sum to ~100 when set)
  federation_percent?: number;
  border_percent?: number;
  frontier_percent?: number;

  // Density knobs
  port_percent?: number;
  planet_percent?: number;
  nebula_percent?: number;

  // Topology
  max_warps?: number;
  one_way_warp_percent?: number;

  // Expert / dev toggles
  validator_strictness?: BangValidatorStrictness;
  stardock_enabled?: boolean;
}

/** Payload for POST /admin/galaxy/jobs (mirrors `BangJobCreate`). */
export interface BangJobCreate {
  config: BangConfig;
  galaxy_name?: string;
}

export type BangJobStatus = 'PENDING' | 'RUNNING' | 'COMPLETE' | 'FAILED';

/**
 * Warning surfaced by bang or the gameserver-side validator. The `code`
 * is a stable `B-NNN` identifier — see `errorCodeMap.ts`.
 */
export interface BangJobWarning {
  category: string;
  code: string;
  message: string;
  data?: Record<string, unknown> | null;
}

/** Full job record returned from `POST /jobs` and `GET /jobs/{id}`. */
export interface BangJobResponse {
  id: string;
  admin_user_id: string;
  status: BangJobStatus;
  params_json: BangConfig & Record<string, unknown>;
  started_at: string;
  completed_at?: string | null;
  duration_ms?: number | null;
  error_message?: string | null;
  warnings_json: BangJobWarning[];
  log_text: string;
}

/** Stats card payload from POST /admin/galaxy/preview. */
export interface BangPreviewStats {
  diameter?: number;
  cluster_count?: number;
  max_warps_histogram?: Record<string, number>;
  formation_counts?: Record<string, number>;
  validator_pass_count?: number;
  total_sectors?: number;
  island_percent?: number;
  // Bang's stats blob is open-ended; allow arbitrary extra fields.
  [key: string]: unknown;
}

export interface BangPreviewValidation {
  passed: boolean;
  rules_run?: number;
  rules_failed?: number;
  [key: string]: unknown;
}

export interface BangPreviewResponse {
  stats: BangPreviewStats;
  warnings: BangJobWarning[];
  validation: BangPreviewValidation;
}

/** Paginated history list. Matches gameserver BangJobListResponse. */
export interface BangJobHistoryPage {
  items: BangJobResponse[];
  total: number;
  page: number;
  page_size: number;
}

/** Convenience: blank/default config used by the form on first mount. */
export const DEFAULT_BANG_CONFIG: BangConfig = {
  seed: 0,
  sectors: 1000,
  region_type: 'player_owned',
  federation_percent: 20,
  border_percent: 30,
  frontier_percent: 50,
};
