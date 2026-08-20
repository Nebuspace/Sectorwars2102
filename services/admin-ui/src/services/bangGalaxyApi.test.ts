import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../utils/auth';
import {
  addPlayerOwnedRegion,
  createBangJob,
  listBangJobs,
  previewBangConfig,
  wipeBangGalaxy,
} from './bangGalaxyApi';
import type { BangConfig, BangJobCreate } from '../components/universe/bang/types';

vi.mock('../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

const payload = { galaxy_name: 'Test Galaxy' } as BangJobCreate;
const config = {} as BangConfig;
const job = { id: 'job-1' };

describe('bangGalaxyApi (shared api client)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.delete).mockReset();
  });

  it('createBangJob posts /api/v1/admin/galaxy/jobs with Bearer overlay', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: job });
    await expect(createBangJob(payload, 'tok')).resolves.toEqual(job);
    expect(api.post).toHaveBeenCalledWith('/api/v1/admin/galaxy/jobs', payload, {
      headers: { Authorization: 'Bearer tok' },
    });
  });

  it('createBangJob omits Authorization when token is null', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: job });
    await createBangJob(payload, null);
    expect(api.post).toHaveBeenCalledWith('/api/v1/admin/galaxy/jobs', payload, {
      headers: {},
    });
  });

  it('addPlayerOwnedRegion posts /api/v1/admin/galaxy/{id}/regions', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: job });
    await addPlayerOwnedRegion('gal-9', payload, 'tok');
    expect(api.post).toHaveBeenCalledWith(
      '/api/v1/admin/galaxy/gal-9/regions',
      payload,
      { headers: { Authorization: 'Bearer tok' } },
    );
  });

  it('previewBangConfig posts /api/v1/admin/galaxy/preview', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { ok: true } });
    await previewBangConfig(config, 'tok');
    expect(api.post).toHaveBeenCalledWith('/api/v1/admin/galaxy/preview', config, {
      headers: { Authorization: 'Bearer tok' },
    });
  });

  it('listBangJobs gets /api/v1/admin/galaxy/jobs with page params', async () => {
    const page = { items: [], total: 0 };
    vi.mocked(api.get).mockResolvedValue({ data: page });
    await expect(listBangJobs(2, 25, 'tok')).resolves.toEqual(page);
    expect(api.get).toHaveBeenCalledWith('/api/v1/admin/galaxy/jobs', {
      params: { page: 2, page_size: 25 },
      headers: { Authorization: 'Bearer tok' },
    });
  });

  it('wipeBangGalaxy deletes with confirm-name header and Bearer', async () => {
    vi.mocked(api.delete).mockResolvedValue({});
    await wipeBangGalaxy('gal-9', 'Test Galaxy', 'tok');
    expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/galaxy/gal-9', {
      headers: {
        Authorization: 'Bearer tok',
        'X-Confirm-Galaxy-Name': 'Test Galaxy',
      },
    });
  });
});
