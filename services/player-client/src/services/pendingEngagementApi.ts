/**
 * Pending police engagement summaries (LEG-902 / PR #722 contract).
 * GET /api/v1/pending-engagements — server-owned countdown; client displays only.
 */
import apiClient from './apiClient';

export interface PendingEngagementSummary {
  id: string;
  jurisdiction: string | null;
  offense_type: string | null;
  squad: string[];
  officer_names: string[];
  turns_to_arrival: number;
  grace_window: string | null;
}

export interface PendingEngagementListResponse {
  items: PendingEngagementSummary[];
}

/** Normalize a WS frame or GET item into the summary shape. */
export function parsePendingEngagementSummary(
  raw: Record<string, unknown>
): PendingEngagementSummary {
  const squad = Array.isArray(raw.squad) ? raw.squad.map(String) : [];
  const officerNames = Array.isArray(raw.officer_names)
    ? raw.officer_names.map(String)
    : squad;
  const turns =
    typeof raw.turns_to_arrival === 'number'
      ? Math.max(0, Math.floor(raw.turns_to_arrival))
      : 0;

  return {
    id: String(raw.id ?? ''),
    jurisdiction: raw.jurisdiction != null ? String(raw.jurisdiction) : null,
    offense_type: raw.offense_type != null ? String(raw.offense_type) : null,
    squad,
    officer_names: officerNames,
    turns_to_arrival: turns,
    grace_window: raw.grace_window != null ? String(raw.grace_window) : null,
  };
}

export const pendingEngagementApi = {
  async listMine(): Promise<PendingEngagementSummary[]> {
    const response = await apiClient.get<PendingEngagementListResponse>(
      '/api/v1/pending-engagements',
      {
        validateStatus: (status) =>
          (status >= 200 && status < 300) || status === 204,
      }
    );

    if (response.status === 204) {
      return [];
    }

    const items = response.data?.items;
    if (!Array.isArray(items)) {
      return [];
    }

    return items.map((item) =>
      parsePendingEngagementSummary(item as Record<string, unknown>)
    );
  },
};

export default pendingEngagementApi;
