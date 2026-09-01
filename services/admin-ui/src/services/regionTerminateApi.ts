/**
 * Admin region termination (LEG-3206 / LEG-DEC-103).
 * GS routes: GET/POST /api/v1/admin/regions/{id}/terminate-preview|terminate
 */
import { api } from '../utils/auth';

export interface RegionTerminatePreview {
  regionId: string;
  regionName: string;
  displayName: string;
  status: string;
  regionType: string;
  planetCount: number;
  stationCount: number;
  sectorCount: number;
  playerStakeholderCount: number;
  terminable: boolean;
}

export async function fetchRegionTerminatePreview(
  regionId: string,
): Promise<RegionTerminatePreview> {
  const { data } = await api.get<RegionTerminatePreview>(
    `/api/v1/admin/regions/${regionId}/terminate-preview`,
  );
  return data;
}

export async function postRegionTerminate(
  regionId: string,
  confirmRegionName: string,
  reason: string,
): Promise<Record<string, unknown>> {
  const { data } = await api.post<Record<string, unknown>>(
    `/api/v1/admin/regions/${regionId}/terminate`,
    { reason },
    {
      headers: {
        'X-Confirm-Region-Name': confirmRegionName,
      },
    },
  );
  return data;
}
