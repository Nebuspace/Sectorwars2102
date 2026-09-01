import React, { useCallback, useMemo, useState } from 'react';
import { useToast } from '../../contexts/ToastContext';
import { formatAdminApiError } from '../../utils/adminApiError';
import {
  GOLD_BUBBLE_GATEWAY_COUNT_MAX,
  GOLD_BUBBLE_GATEWAY_COUNT_MIN,
  GOLD_BUBBLE_INTERIOR_SIZE_MIN,
  assertValidUuidList,
  parseSectorIdList,
  placeGoldBubble,
  type PlaceGoldBubbleFormation,
} from '../../services/placeGoldBubbleApi';

const GALAXY_MANAGE_SCOPE_HINT =
  'admin.galaxy.manage (GALAXY_MANAGE) scope required for place_gold_bubble';

const GAMESERVER_UNREACHABLE =
  'Network error — could not reach the gameserver. Check your connection and try again.';

function isTransportCollapse(err: unknown): boolean {
  if (err instanceof TypeError) return true;
  if (!(err instanceof Error)) return false;
  const msg = err.message.trim();
  return msg === '' || msg === 'Network Error';
}

export function formatPlaceGoldBubbleError(err: unknown): string {
  if (isTransportCollapse(err)) {
    return GAMESERVER_UNREACHABLE;
  }
  if (err instanceof Error && !('response' in err)) {
    return err.message;
  }
  return formatAdminApiError(err, {
    fallback: 'Gold Bubble placement failed',
    scopeHint: GALAXY_MANAGE_SCOPE_HINT,
    notFoundMessage:
      'Region or sector not found (404). Confirm region_id and that every sector UUID belongs to that region.',
  });
}

export interface PlaceGoldBubblePanelProps {
  /** Optional region options from AdminContext; operator may still paste a UUID. */
  regions?: Array<{ id: string; display_name?: string; name?: string }>;
}

/**
 * Operator-only GOLD_BUBBLE hand placement (LEG-184).
 * Calls POST …/formations/gold-bubble — never random generation.
 */
const PlaceGoldBubblePanel: React.FC<PlaceGoldBubblePanelProps> = ({
  regions = [],
}) => {
  const toast = useToast();
  const [regionId, setRegionId] = useState('');
  const [gatewayRaw, setGatewayRaw] = useState('');
  const [interiorRaw, setInteriorRaw] = useState('');
  const [name, setName] = useState('');
  const [isolateWarps, setIsolateWarps] = useState(true);
  const [discoveryJson, setDiscoveryJson] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [lastFormation, setLastFormation] =
    useState<PlaceGoldBubbleFormation | null>(null);

  const gatewayIds = useMemo(() => parseSectorIdList(gatewayRaw), [gatewayRaw]);
  const interiorIds = useMemo(
    () => parseSectorIdList(interiorRaw),
    [interiorRaw],
  );

  const clientHint = useMemo(() => {
    const parts: string[] = [];
    if (
      gatewayIds.length < GOLD_BUBBLE_GATEWAY_COUNT_MIN ||
      gatewayIds.length > GOLD_BUBBLE_GATEWAY_COUNT_MAX
    ) {
      parts.push(
        `Gateways: ${gatewayIds.length} (need ${GOLD_BUBBLE_GATEWAY_COUNT_MIN}–${GOLD_BUBBLE_GATEWAY_COUNT_MAX})`,
      );
    }
    if (interiorIds.length < GOLD_BUBBLE_INTERIOR_SIZE_MIN) {
      parts.push(
        `Interior: ${interiorIds.length} (need ≥${GOLD_BUBBLE_INTERIOR_SIZE_MIN})`,
      );
    }
    return parts.join(' · ');
  }, [gatewayIds.length, interiorIds.length]);

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setFormError(null);
      setLastFormation(null);

      const rid = regionId.trim();
      if (!rid) {
        setFormError('Region UUID is required.');
        return;
      }

      try {
        assertValidUuidList([rid], 'region_id');
        assertValidUuidList(gatewayIds, 'gateway_sector_ids');
        assertValidUuidList(interiorIds, 'interior_sector_ids');
      } catch (err) {
        setFormError(formatPlaceGoldBubbleError(err));
        return;
      }

      if (
        gatewayIds.length < GOLD_BUBBLE_GATEWAY_COUNT_MIN ||
        gatewayIds.length > GOLD_BUBBLE_GATEWAY_COUNT_MAX
      ) {
        setFormError(
          `gateway_sector_ids must contain ${GOLD_BUBBLE_GATEWAY_COUNT_MIN}–${GOLD_BUBBLE_GATEWAY_COUNT_MAX} UUIDs (got ${gatewayIds.length}).`,
        );
        return;
      }
      if (interiorIds.length < GOLD_BUBBLE_INTERIOR_SIZE_MIN) {
        setFormError(
          `interior_sector_ids must contain ≥${GOLD_BUBBLE_INTERIOR_SIZE_MIN} UUIDs (got ${interiorIds.length}).`,
        );
        return;
      }

      let discovery_requirement: Record<string, unknown> | undefined;
      const discTrim = discoveryJson.trim();
      if (discTrim) {
        try {
          const parsed = JSON.parse(discTrim) as unknown;
          if (
            typeof parsed !== 'object' ||
            parsed === null ||
            Array.isArray(parsed)
          ) {
            setFormError('discovery_requirement must be a JSON object when set.');
            return;
          }
          discovery_requirement = parsed as Record<string, unknown>;
        } catch {
          setFormError('discovery_requirement JSON is invalid.');
          return;
        }
      }

      const body = {
        gateway_sector_ids: gatewayIds,
        interior_sector_ids: interiorIds,
        isolate_warps: isolateWarps,
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(discovery_requirement ? { discovery_requirement } : {}),
      };

      setSubmitting(true);
      try {
        const result = await placeGoldBubble(rid, body);
        setLastFormation(result.formation);
        toast.success(
          `Gold Bubble placed (${result.formation.id}) — type ${result.formation.type}`,
        );
      } catch (err) {
        const message = formatPlaceGoldBubbleError(err);
        setFormError(message);
        toast.error(message);
      } finally {
        setSubmitting(false);
      }
    },
    [
      discoveryJson,
      gatewayIds,
      interiorIds,
      isolateWarps,
      name,
      regionId,
      toast,
    ],
  );

  return (
    <section
      className="place-gold-bubble-panel"
      aria-labelledby="place-gold-bubble-heading"
      style={{
        marginTop: '1.5rem',
        padding: '1rem 1.25rem',
        border: '1px solid #374151',
        borderRadius: '8px',
        background: 'rgba(17, 24, 39, 0.65)',
      }}
    >
      <h3 id="place-gold-bubble-heading" style={{ marginTop: 0 }}>
        Place Gold Bubble
      </h3>
      <p style={{ fontSize: '0.9rem', color: '#9ca3af', marginTop: 0 }}>
        Operator hand-placement only (canon: not in the random Bang budget). Posts to{' '}
        <code>place_gold_bubble</code> — requires{' '}
        <code>admin.galaxy.manage</code>. Interior must be ≥
        {GOLD_BUBBLE_INTERIOR_SIZE_MIN} sector UUIDs; gateways 1–3 (first is primary
        anchor).
      </p>

      <form onSubmit={onSubmit}>
        <div style={{ marginBottom: '0.75rem' }}>
          <label htmlFor="pgb-region">
            Region
            {regions.length > 0 ? ' (select or paste UUID)' : ' UUID'}
          </label>
          {regions.length > 0 && (
            <select
              id="pgb-region-select"
              aria-label="Select region for Gold Bubble"
              value={regions.some((r) => r.id === regionId) ? regionId : ''}
              onChange={(e) => setRegionId(e.target.value)}
              style={{ display: 'block', width: '100%', marginBottom: '0.35rem' }}
              disabled={submitting}
            >
              <option value="">— choose region —</option>
              {regions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.display_name || r.name || r.id}
                </option>
              ))}
            </select>
          )}
          <input
            id="pgb-region"
            type="text"
            value={regionId}
            onChange={(e) => setRegionId(e.target.value)}
            placeholder="region UUID"
            autoComplete="off"
            disabled={submitting}
            style={{ display: 'block', width: '100%' }}
          />
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label htmlFor="pgb-gateways">
            Gateway sector UUIDs (1–3, comma or newline separated)
          </label>
          <textarea
            id="pgb-gateways"
            rows={3}
            value={gatewayRaw}
            onChange={(e) => setGatewayRaw(e.target.value)}
            disabled={submitting}
            style={{ display: 'block', width: '100%', fontFamily: 'monospace' }}
          />
          <small style={{ color: '#9ca3af' }}>Parsed: {gatewayIds.length}</small>
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label htmlFor="pgb-interior">
            Interior sector UUIDs (≥{GOLD_BUBBLE_INTERIOR_SIZE_MIN})
          </label>
          <textarea
            id="pgb-interior"
            rows={6}
            value={interiorRaw}
            onChange={(e) => setInteriorRaw(e.target.value)}
            disabled={submitting}
            style={{ display: 'block', width: '100%', fontFamily: 'monospace' }}
          />
          <small style={{ color: '#9ca3af' }}>
            Parsed: {interiorIds.length}
            {clientHint ? ` — ${clientHint}` : ''}
          </small>
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label htmlFor="pgb-name">Name (optional)</label>
          <input
            id="pgb-name"
            type="text"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={submitting}
            style={{ display: 'block', width: '100%' }}
          />
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label htmlFor="pgb-discovery">
            discovery_requirement JSON (optional — pass-through only)
          </label>
          <textarea
            id="pgb-discovery"
            rows={3}
            value={discoveryJson}
            onChange={(e) => setDiscoveryJson(e.target.value)}
            placeholder='e.g. {"kind":"…"}'
            disabled={submitting}
            style={{ display: 'block', width: '100%', fontFamily: 'monospace' }}
          />
        </div>

        <div style={{ marginBottom: '0.75rem' }}>
          <label>
            <input
              type="checkbox"
              checked={isolateWarps}
              onChange={(e) => setIsolateWarps(e.target.checked)}
              disabled={submitting}
            />{' '}
            isolate_warps (Phase B envelope; default on)
          </label>
        </div>

        {formError && (
          <div role="alert" style={{ color: '#fca5a5', marginBottom: '0.75rem' }}>
            {formError}
          </div>
        )}

        {lastFormation && (
          <div
            role="status"
            style={{
              color: '#86efac',
              marginBottom: '0.75rem',
              fontSize: '0.9rem',
            }}
          >
            Placed <strong>{lastFormation.type}</strong> id=
            <code>{lastFormation.id}</code> · interior=
            {lastFormation.interior_sector_ids?.length ?? 0} · anchor=
            <code>{lastFormation.anchor_sector_id}</code>
          </div>
        )}

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? 'Placing…' : 'Place Gold Bubble'}
        </button>
      </form>
    </section>
  );
};

export default PlaceGoldBubblePanel;
