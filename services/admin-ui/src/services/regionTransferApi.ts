/**
 * Admin region ownership transfer (LEG-3967 / LEG-DEC-500).
 * GS route: POST /api/v1/admin/regions/{id}/transfer-ownership
 */
import { api } from '../utils/auth';

export interface RegionTransferOwnershipResult {
  regionId: string;
  previousOwnerId: string;
  newOwnerId: string;
  regionName: string;
  displayName: string;
}

export async function postRegionTransferOwnership(
  regionId: string,
  newOwnerId: string,
  reason: string,
): Promise<RegionTransferOwnershipResult> {
  const { data } = await api.post<RegionTransferOwnershipResult>(
    `/api/v1/admin/regions/${regionId}/transfer-ownership`,
    { newOwnerId, reason },
  );
  return data;
}
