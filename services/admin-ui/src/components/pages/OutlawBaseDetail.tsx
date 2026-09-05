import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';

/**
 * LEG-4212 — read-only OutlawBase inspect (deep-link target for LEG-4196).
 * Loads GET /api/v1/admin/outlaw-bases/{id}; displays committed fields only.
 */

interface OutlawBaseDetailPayload {
  id: string;
  name: string | null;
  sector_id: number | null;
  home_region_id: string | null;
  faction_code: string | null;
  archetype: string | null;
  capacity: number | null;
  current_occupants_count: number | null;
  is_player_discoverable: boolean | null;
  raid_cooldown_until: string | null;
  last_raided_at: string | null;
  relocation_pending: boolean | null;
}

const DISPLAY_FIELDS: Array<keyof OutlawBaseDetailPayload> = [
  'id',
  'name',
  'sector_id',
  'home_region_id',
  'faction_code',
  'archetype',
  'capacity',
  'current_occupants_count',
  'is_player_discoverable',
  'raid_cooldown_until',
  'last_raided_at',
  'relocation_pending',
];

function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

const OutlawBaseDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [base, setBase] = useState<OutlawBaseDetailPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) {
      setError('OutlawBase id is required');
      setBase(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await api.get(`/api/v1/admin/outlaw-bases/${id}`);
      setBase(response.data as OutlawBaseDetailPayload);
    } catch (err: unknown) {
      setBase(null);
      setError(
        formatAdminApiError(err, {
          fallback: 'Failed to load OutlawBase',
          scopeHint: 'PLAYERS_VIEW scope required to inspect outlaw bases',
          notFoundMessage: 'OutlawBase not found',
        }),
      );
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const title = base?.name ? `Outlaw Base: ${base.name}` : 'Outlaw Base';

  return (
    <div className="page-container" data-testid="outlaw-base-detail">
      <header className="mb-6">
        <h1 className="page-title">{title}</h1>
        <p className="page-subtitle">Read-only operator inspect</p>
      </header>

      {loading ? (
        <div className="loading-container text-center py-12">
          <div className="loading-spinner mx-auto mb-4" />
          <span>Loading OutlawBase…</span>
        </div>
      ) : null}

      {error ? (
        <div className="alert alert-error mb-6" role="alert" data-testid="outlaw-base-error">
          {error}
        </div>
      ) : null}

      {!loading && !error && base ? (
        <section className="section">
          <div className="card">
            <div className="card-body">
              <dl data-testid="outlaw-base-fields">
                {DISPLAY_FIELDS.map((field) => (
                  <div key={field} style={{ display: 'flex', gap: '1rem', marginBottom: '0.5rem' }}>
                    <dt style={{ minWidth: '12rem', fontWeight: 600 }}>{field}</dt>
                    <dd data-testid={`outlaw-base-field-${field}`}>
                      {formatFieldValue(base[field])}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
};

export default OutlawBaseDetail;
