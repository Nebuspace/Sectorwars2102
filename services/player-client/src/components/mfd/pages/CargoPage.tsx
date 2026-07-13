/**
 * CARGO BAY — MFD-A page (NEON15, zone B2).
 *
 * Cargo shape handling mirrors the retired sidebar (GameLayout.tsx) exactly:
 * the server may serialize ship cargo as {used, capacity, contents: {good: qty}}
 * OR as a flat {good: qty} map — both are handled, nothing invented. Capacity
 * additionally falls back to the typed Ship.cargo_capacity field when the
 * cargo dict omits it.
 *
 * Visual re-emit (WO-UI-MAX-BATCH-1, mfd-crgo lane): matches the ratified
 * demo's HOLD TANK + cargo STACK visual (cockpit-redesign-v10-RATIFIED.html
 * L1302-1321) — a 12-segment tank gauge that fills proportionally to
 * used/capacity, plus a proportional composition bar and a color-swatched
 * legend for held commodities. Per-commodity color/label are read from the
 * shared resource catalog (useResourceCatalog) rather than the demo's fixed
 * 5-key palette: the demo only ever needed to color a handful of fictional
 * goods, but a real hold can carry any of the registry's commodities, and
 * resourceColor()/resourceLabel() is the established single source every
 * other trading surface already draws from (resourceCatalog.ts) — reusing
 * it beats hardcoding a second, narrower palette.
 *
 * Genesis bay slot visual is untouched by this pass — it's a pre-existing
 * lamp row (◉ loaded / ○ empty, glow-animated) shared verbatim with
 * VesselPage.tsx via pages-ship.css, out of this page's WO fence.
 */
import React, { useState } from 'react';
import { useGame } from '../../../contexts/GameContext';
import { useResourceCatalog } from '../../../hooks/useResourceCatalog';
import { MFDPageHeader, MFDPageBody, MFDEmpty } from '../atoms';
import { GenesisDeployment } from '../../planetary/GenesisDeployment';
import './pages-ship.css';
import './pages-cargo.css';

const ACCENT = '#9EC5FF';
const TANK_SEGMENTS = 12;

const asRecord = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

const CargoPage: React.FC = () => {
  const { currentShip } = useGame();
  const { getLabel, getColor } = useResourceCatalog();
  const [showGenesis, setShowGenesis] = useState(false);

  if (!currentShip) {
    return (
      <>
        <MFDPageHeader title="CARGO BAY" accent={ACCENT} status="shipped" showTitle={false} />
        <MFDPageBody scrollKey="cargo">
          <MFDEmpty text="NO ACTIVE VESSEL" />
        </MFDPageBody>
      </>
    );
  }

  const cargo = asRecord(currentShip.cargo as unknown) ?? {};
  const contentsRec = asRecord(cargo['contents']);
  const entries: Array<[string, number]> = contentsRec
    ? Object.entries(contentsRec).flatMap(([k, v]) =>
        typeof v === 'number' ? [[k, v] as [string, number]] : [],
      )
    : Object.entries(cargo).flatMap(([k, v]) =>
        typeof v === 'number' && !['used', 'capacity'].includes(k)
          ? [[k, v] as [string, number]]
          : [],
      );
  const items = entries.filter(([, qty]) => qty > 0);
  const itemsTotal = items.reduce((sum, [, qty]) => sum + qty, 0);

  const used = num(cargo['used']);
  const capacity = num(cargo['capacity']) ?? num(currentShip.cargo_capacity);
  const holdLabel =
    used !== null || capacity !== null ? `${used ?? '—'} / ${capacity ?? '—'}` : '— / —';
  // Round-to-nearest-segment fill, same convention as the demo's
  // `Math.round(cargoUsed()/G.hold*segs)` — only computable when both legs
  // of the ratio are real numbers; otherwise the tank renders empty rather
  // than guessing a fill level.
  const filledSegments =
    used !== null && capacity !== null && capacity > 0
      ? Math.max(0, Math.min(TANK_SEGMENTS, Math.round((used / capacity) * TANK_SEGMENTS)))
      : 0;

  const maxGenesis = num(currentShip.max_genesis_devices) ?? 0;
  const loadedGenesis = num(currentShip.genesis_devices) ?? 0;

  return (
    <>
      <MFDPageHeader title="CARGO BAY" accent={ACCENT} status="shipped" showTitle={false} />
      <MFDPageBody scrollKey="cargo">
        <div className="mfd-cargo-tank">
          <svg
            viewBox="0 0 100 34"
            className="mfd-cargo-tank-svg"
            role="img"
            aria-label={`Cargo hold ${holdLabel}`}
          >
            <text x="6" y="6.5" className="mfd-cargo-tank-label">
              HOLD {holdLabel}
            </text>
            <rect x="6" y="10" width="88" height="15" rx="2" className="mfd-cargo-tank-frame" />
            {Array.from({ length: TANK_SEGMENTS }, (_, i) => (
              <rect
                key={i}
                x={8 + i * 7.2}
                y="12"
                width="5.6"
                height="11"
                rx="1"
                className={
                  i < filledSegments ? 'mfd-cargo-tank-seg filled' : 'mfd-cargo-tank-seg'
                }
              />
            ))}
          </svg>
        </div>

        <div className="mfd-page-section">
          <div className="mfd-page-section-title">MANIFEST</div>
          {items.length > 0 ? (
            <>
              <svg
                viewBox="0 0 100 10"
                className="mfd-cargo-stack-svg"
                role="img"
                aria-label={`Cargo composition: ${items
                  .map(([resource, qty]) => `${getLabel(resource)} ${qty}`)
                  .join(', ')}`}
              >
                {(() => {
                  let cx = 6;
                  const barWidth = 88;
                  return items.map(([resource, qty]) => {
                    const w = itemsTotal > 0 ? (qty / itemsTotal) * barWidth : 0;
                    const seg = (
                      <rect
                        key={resource}
                        x={cx}
                        y="2"
                        width={Math.max(1.5, w - 0.8)}
                        height="6"
                        rx="1"
                        fill={getColor(resource)}
                        opacity={0.85}
                      />
                    );
                    cx += w;
                    return seg;
                  });
                })()}
              </svg>
              <ul className="mfd-page-cargo-list">
                {items.map(([resource, qty]) => (
                  <li key={resource} className="mfd-page-cargo-row">
                    <span className="mfd-page-cargo-name">
                      <span
                        className="mfd-cargo-swatch"
                        style={{ backgroundColor: getColor(resource) }}
                        aria-hidden="true"
                      />
                      {getLabel(resource)}
                    </span>
                    <span className="mfd-page-cargo-qty">× {qty}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <MFDEmpty text="CARGO BAY EMPTY" />
          )}
        </div>

        {maxGenesis > 0 && (
          <div className="mfd-page-section">
            <div className="mfd-page-section-title">GENESIS BAY</div>
            <div className="mfd-page-genesis-row">
              <div className="mfd-page-genesis-slots">
                {Array.from({ length: maxGenesis }, (_, i) => {
                  const loaded = i < loadedGenesis;
                  return (
                    <span
                      key={i}
                      className={`mfd-page-genesis-slot ${loaded ? 'loaded' : 'empty'}`}
                      title={loaded ? 'Genesis Device Loaded' : 'Empty Slot'}
                    >
                      {loaded ? '◉' : '○'}
                    </span>
                  );
                })}
              </div>
              <span
                className={`mfd-page-genesis-count${loadedGenesis > 0 ? ' active' : ''}`}
              >
                {loadedGenesis} / {maxGenesis}
              </span>
            </div>
            {loadedGenesis > 0 && (
              <button
                type="button"
                className="mfd-page-genesis-ready mfd-page-genesis-deploy-btn"
                onClick={() => setShowGenesis(true)}
                title="Deploy a Genesis Device — seed a new colony in an empty sector"
              >
                <span className="mfd-page-genesis-ready-dot" />
                TERRAFORM READY — DEPLOY
              </button>
            )}
          </div>
        )}
      </MFDPageBody>

      {showGenesis && (
        <div className="genesis-modal-overlay" onClick={() => setShowGenesis(false)}>
          <div className="genesis-modal-content" onClick={(e) => e.stopPropagation()}>
            <GenesisDeployment onClose={() => setShowGenesis(false)} onSuccess={() => setShowGenesis(false)} />
          </div>
        </div>
      )}
    </>
  );
};

export default React.memo(CargoPage);
