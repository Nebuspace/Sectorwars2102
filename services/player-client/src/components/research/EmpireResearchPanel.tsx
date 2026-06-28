import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { researchCockpitAPI } from '../../services/api';
import { useWebSocket } from '../../contexts/WebSocketContext';
import './empire-research-panel.css';

/**
 * EmpireResearchPanel — CRT-T1.5-9 / CRT-4: the player-facing capstone of the
 * governed-flywheel economy ("Citadel Research", Max-ruled name). This is the ONE
 * glanceable, EMPIRE-LEVEL surface where the player sees the LOOP they regulate
 * (more labs → RP → governed → spend on directives → frontier decays → …). It is
 * notification-driven: a healthy empire needs ~0 clicks/day — the offers are
 * GENERATED + pushed (contract_offer WS frame), never browsed (§5.3). A
 * done/uncontested world raises no offer, so a finished empire's offer list is
 * simply empty.
 *
 * Three read surfaces (§5.4/§5.5/§5.7), no graphs:
 *   1. §5.4 R&D summary  — four numbers, one line each: RP/day in → spent/banked
 *      → contracts active → worlds frontier vs done. GET /research/cockpit.
 *   2. §5.5 headroom      — "RP/day: N (throughput X% — finishing worlds lifts
 *      this)". One number + one trend so "bends, never clips" is VISIBLE.
 *      Copy is day-one TRUE: it names no non-existent lever (no "Doctrine" — the
 *      GOV_DOCTRINE_LIFT lever does not exist in T1.5, §5.5-A/§5.10).
 *   3. §5.7 offers        — the GENERATED, perishable Research-Directive offers
 *      with accept/ignore. An ignored offer perishes FREE + clears (§5.9 #4).
 *      GET /research/offers; POST /research/contracts/start to accept.
 *
 * Live: a contract_offer / contract_settled / rp_governor_status push bumps the
 * WebSocketContext researchEventSignal, which re-fetches the cockpit + offers so
 * the panel stays current without a poll.
 */

// ----- FROZEN cross-zone contract types (backend produces, client consumes) -----

interface ResearchCockpit {
  rpPerDay?: number;
  rpThroughputPct?: number;
  banked?: number;
  spent?: number;
  contractsActive?: number;
  worldsFrontier?: number;
  worldsDone?: number;
  governorHeadroom?: number;
  softCap?: number;
}

interface ResearchOffer {
  id: string;
  kind: string;
  planetId: string;
  planetName: string;
  rpCost: number;
  crCost: number;
  magnitude: number;
  expiresAt: string | null;
}

interface OffersResponse {
  offers?: ResearchOffer[];
}

// ----- Helpers (mirror the CRT design-language formatting in adjacent panels) ---

/** Compact magnitude: 1.2M / 50k / 300 (same idiom as GridManager/CitadelManager). */
const compact = (n: number): string => {
  if (!Number.isFinite(n)) return '0';
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${n % 1_000_000 === 0 ? n / 1_000_000 : (n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${n % 1_000 === 0 ? n / 1_000 : (n / 1_000).toFixed(1)}k`;
  return `${n}`;
};

/** Human label per directive kind (the kernel set is Overclock + Rush). */
const KIND_LABEL: Record<string, string> = {
  overclock: 'Overclock',
  rush: 'Rush',
  stabilize: 'Stabilize',
};

/** In-fiction one-liner per kind so the offer reads as a choice, not a number. */
const KIND_BLURB: Record<string, string> = {
  overclock: 'Push a world past its rated output for a few days.',
  rush: 'Collapse a live build or terraform timer instantly.',
  stabilize: 'Bleed off instability on a contested world.',
};

const kindLabel = (kind: string): string =>
  KIND_LABEL[(kind || '').toLowerCase()] || (kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : 'Directive');

const kindBlurb = (kind: string): string =>
  KIND_BLURB[(kind || '').toLowerCase()] || 'A research directive on one of your worlds.';

/** A short "perishes in 4h" string from an ISO expiry, or null when open-ended. */
const expiresIn = (iso: string | null, nowMs: number): string | null => {
  if (!iso) return null;
  const end = Date.parse(iso);
  if (!Number.isFinite(end)) return null;
  const ms = end - nowMs;
  if (ms <= 0) return 'expired';
  const totalMin = Math.floor(ms / 60_000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
};

const EmpireResearchPanel: React.FC = () => {
  const { researchEventSignal, lastGovernorStatus } = useWebSocket();

  const [cockpit, setCockpit] = useState<ResearchCockpit | null>(null);
  const [offers, setOffers] = useState<ResearchOffer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null); // offer id in flight
  const [actionMessage, setActionMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  // Locally-ignored offer ids — an ignored offer perishes free + clears (§5.9 #4)
  // without a server round-trip (the offer expires on its own server-side).
  const [ignored, setIgnored] = useState<Set<string>>(() => new Set());
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  const fetchAll = useCallback(async (showSpinner: boolean) => {
    if (showSpinner) setLoading(true);
    try {
      // Resilient: a failure on one read shouldn't blank the other surface.
      const [cockpitRes, offersRes] = await Promise.allSettled([
        researchCockpitAPI.getCockpit(),
        researchCockpitAPI.getOffers(),
      ]);
      if (cockpitRes.status === 'fulfilled') {
        setCockpit(cockpitRes.value as ResearchCockpit);
        setError(null);
      } else if (showSpinner) {
        setError((cockpitRes.reason as any)?.message || 'Failed to load research cockpit');
      }
      if (offersRes.status === 'fulfilled') {
        const data = offersRes.value as OffersResponse;
        setOffers(Array.isArray(data?.offers) ? data.offers : []);
      }
    } finally {
      if (showSpinner) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll(true);
  }, [fetchAll]);

  // Live refresh: a pushed contract_offer / contract_settled / rp_governor_status
  // frame bumps researchEventSignal — re-fetch the cockpit + offers silently so
  // the panel reflects the new directive without a poll. Skip the initial mount
  // (signal 0); the mount effect above owns the first load.
  const researchSignalRef = useRef(researchEventSignal);
  useEffect(() => {
    if (researchEventSignal === researchSignalRef.current) return;
    researchSignalRef.current = researchEventSignal;
    fetchAll(false);
  }, [researchEventSignal, fetchAll]);

  // Tick once a second only while an offer carries an expiry, purely to animate
  // the "perishes in" countdown.
  const anyExpiring = useMemo(() => offers.some((o) => o.expiresAt), [offers]);
  useEffect(() => {
    if (!anyExpiring) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [anyExpiring]);

  const handleAccept = useCallback(
    async (offer: ResearchOffer) => {
      if (actionLoading) return;
      try {
        setActionLoading(offer.id);
        setActionMessage(null);
        await researchCockpitAPI.startContract({ offerId: offer.id, planetId: offer.planetId });
        setActionMessage({
          kind: 'ok',
          text: `${kindLabel(offer.kind)} started${offer.planetName ? ` on ${offer.planetName}` : ''}.`,
        });
        await fetchAll(false);
      } catch (err: any) {
        // apiRequest surfaces the server's human message (402 credits / 4xx gate).
        setActionMessage({ kind: 'err', text: err?.message || 'Could not start directive' });
      } finally {
        setActionLoading(null);
      }
    },
    [actionLoading, fetchAll],
  );

  // Ignore = let it perish free. No charge, no RP — purely clears it from view
  // (the offer expires server-side on its own; §5.3 "offers perish free").
  const handleIgnore = useCallback((offerId: string) => {
    setIgnored((cur) => {
      const next = new Set(cur);
      next.add(offerId);
      return next;
    });
  }, []);

  // ----- Derived summary values (defensive — render gracefully when absent) -----

  const rpPerDay = cockpit?.rpPerDay ?? null;
  // Prefer the live band-cross push (rp_governor_status) for the throughput trend
  // when it is fresher than the last cockpit read; fall back to the cockpit read.
  const throughputPct =
    lastGovernorStatus?.throughputPct ?? cockpit?.rpThroughputPct ?? null;
  const banked = cockpit?.banked ?? null;
  const spent = cockpit?.spent ?? null;
  const contractsActive = cockpit?.contractsActive ?? null;
  const worldsFrontier = cockpit?.worldsFrontier ?? null;
  const worldsDone = cockpit?.worldsDone ?? null;
  const governorHeadroom = cockpit?.governorHeadroom ?? null;

  // "bends, never clips" is VISIBLE: a throughput < 100% means the governor's
  // taper is engaged. Below 100 → tapering; at/over 100 → full throughput.
  const tapering = typeof throughputPct === 'number' && throughputPct < 100;

  const visibleOffers = useMemo(
    () => offers.filter((o) => !ignored.has(o.id) && expiresIn(o.expiresAt, nowMs) !== 'expired'),
    [offers, ignored, nowMs],
  );

  // ----- Render -----

  if (loading) {
    return (
      <div className="empire-research empire-research-loading">
        <div className="er-spinner" />
        <span>Reading empire research telemetry...</span>
      </div>
    );
  }

  if (error && !cockpit) {
    return (
      <div className="empire-research empire-research-error">
        <span>{error}</span>
        <button className="er-retry-btn" onClick={() => fetchAll(true)}>Retry</button>
      </div>
    );
  }

  return (
    <div className="empire-research">
      <div className="er-header">
        <h3>Citadel Research</h3>
        <span
          className="er-scope-badge"
          title="Empire-wide research summary — your governor, contracts, and directives across all worlds."
        >
          🛰️ Empire
        </span>
      </div>

      {/* §5.5 HEADROOM READOUT — one number + one trend, copy TRUE day one.
          Names no non-existent lever (no "Doctrine"): the only real lift in T1.5
          is finishing/expanding worlds. */}
      <div className={`er-headroom${tapering ? ' tapering' : ''}`}>
        <div className="er-headroom-main">
          <span className="er-headroom-label">RP / day</span>
          <span className="er-headroom-value">{rpPerDay !== null ? compact(rpPerDay) : '—'}</span>
          {throughputPct !== null && (
            <span
              className={`er-throughput${tapering ? ' tapering' : ''}`}
              title={
                tapering
                  ? 'Your research is past its full-throughput band — extra labs still yield more, just compressed. Finishing or expanding worlds raises the band.'
                  : 'Your empire is at full research throughput for its current frontier.'
              }
            >
              throughput {throughputPct}%
            </span>
          )}
        </div>
        <div className="er-headroom-copy">
          {tapering
            ? 'Past full throughput — finishing or expanding worlds lifts this. (More labs still help, just less each.)'
            : 'At full throughput for your current frontier — finishing or expanding worlds raises it.'}
          {typeof governorHeadroom === 'number' && governorHeadroom > 0 && (
            <span className="er-headroom-unlock" title="Extra RP/day headroom you unlock by capstoning a world.">
              {' '}Capstoning a world unlocks +{compact(governorHeadroom)} RP/day.
            </span>
          )}
        </div>
      </div>

      {/* §5.4 R&D SUMMARY — four numbers, one line each, no graphs. The LOOP. */}
      <div className="er-summary">
        <div className="er-summary-row">
          <span className="er-row-icon" aria-hidden="true">🔬</span>
          <span className="er-row-label">RP / day in</span>
          <span className="er-row-value">{rpPerDay !== null ? compact(rpPerDay) : '—'}</span>
        </div>
        <div className="er-summary-row">
          <span className="er-row-icon" aria-hidden="true">💠</span>
          <span className="er-row-label">Spent / banked</span>
          <span className="er-row-value">
            {spent !== null ? compact(spent) : '—'} <span className="er-row-sep">/</span>{' '}
            {banked !== null ? compact(banked) : '—'}
          </span>
        </div>
        <div className="er-summary-row">
          <span className="er-row-icon" aria-hidden="true">📜</span>
          <span className="er-row-label">Directives active</span>
          <span className="er-row-value">{contractsActive !== null ? contractsActive : '—'}</span>
        </div>
        <div className="er-summary-row">
          <span className="er-row-icon" aria-hidden="true">🌍</span>
          <span className="er-row-label">Worlds frontier / done</span>
          <span className="er-row-value">
            {worldsFrontier !== null ? worldsFrontier : '—'} <span className="er-row-sep">/</span>{' '}
            {worldsDone !== null ? worldsDone : '—'}
          </span>
        </div>
      </div>

      {/* §5.7 OFFER SURFACE — GENERATED, perishable directives (never a catalogue).
          An ignored offer perishes free + clears; a done empire shows none. */}
      <div className="er-offers">
        <div className="er-offers-head">
          <span className="er-offers-title">Research Directives</span>
          <span className="er-offers-count" title="Generated, perishable offers — accept or let them perish (free).">
            {visibleOffers.length}
          </span>
        </div>

        {visibleOffers.length === 0 ? (
          <div className="er-offers-empty">
            No directives right now — your worlds are running clean. Directives appear
            when a frontier or contested world has something worth pushing.
          </div>
        ) : (
          <div className="er-offers-list">
            {visibleOffers.map((offer) => {
              const perish = expiresIn(offer.expiresAt, nowMs);
              const inFlight = actionLoading === offer.id;
              return (
                <div className="er-offer" key={offer.id}>
                  <div className="er-offer-body">
                    <div className="er-offer-head">
                      <span className="er-offer-kind">{kindLabel(offer.kind)}</span>
                      {offer.planetName && (
                        <span className="er-offer-where" title="Target world">
                          {offer.planetName}
                        </span>
                      )}
                      {perish && (
                        <span
                          className={`er-offer-perish${perish === 'expired' ? ' expired' : ''}`}
                          title="Offers perish on their own — ignoring one costs nothing."
                        >
                          ⏳ {perish === 'expired' ? 'expired' : `perishes in ${perish}`}
                        </span>
                      )}
                    </div>
                    <div className="er-offer-blurb">{kindBlurb(offer.kind)}</div>
                    <div className="er-offer-cost">
                      {typeof offer.crCost === 'number' && (
                        <span className="er-cost-cr" title="Credit cost (the sink)">
                          💰 {compact(offer.crCost)}
                        </span>
                      )}
                      {typeof offer.rpCost === 'number' && (
                        <span className="er-cost-rp" title="Research-point gate">
                          🔬 {compact(offer.rpCost)} RP
                        </span>
                      )}
                      {typeof offer.magnitude === 'number' && offer.magnitude !== 0 && (
                        <span className="er-cost-mag" title="Effect magnitude">
                          ⬆ {offer.magnitude}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="er-offer-actions">
                    <button
                      className="er-btn accept-btn"
                      disabled={inFlight || actionLoading !== null}
                      onClick={() => handleAccept(offer)}
                      title={`Accept this directive — charges the credit sink + RP gate.`}
                    >
                      {inFlight ? 'Starting…' : 'Accept'}
                    </button>
                    <button
                      className="er-btn ignore-btn"
                      disabled={inFlight}
                      onClick={() => handleIgnore(offer.id)}
                      title="Ignore — the offer perishes on its own, free of charge."
                    >
                      Ignore
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {actionMessage && (
        <div className={`er-message ${actionMessage.kind === 'err' ? 'err' : 'ok'}`}>
          {actionMessage.text}
        </div>
      )}
    </div>
  );
};

export default EmpireResearchPanel;
