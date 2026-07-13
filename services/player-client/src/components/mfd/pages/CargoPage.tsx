/**
 * CARGO BAY — MFD-A page (NEON15, zone B2).
 *
 * Cargo shape handling mirrors the retired sidebar (GameLayout.tsx) exactly:
 * the server may serialize ship cargo as {used, capacity, contents: {good: qty}}
 * OR as a flat {good: qty} map — both are handled, nothing invented. Capacity
 * additionally falls back to the typed Ship.cargo_capacity field when the
 * cargo dict omits it. Genesis bay slot visual is ported from the sidebar,
 * restyled with .mfd-page-* classes (text glyphs, no emoji).
 */
import React, { useState } from 'react';
import { useGame } from '../../../contexts/GameContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty } from '../atoms';
import { GenesisDeployment } from '../../planetary/GenesisDeployment';
import './pages-ship.css';

const ACCENT = '#9EC5FF';

const asRecord = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

const formatCommodity = (key: string): string =>
  key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const CargoPage: React.FC = () => {
  const { currentShip } = useGame();
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

  const used = num(cargo['used']);
  const capacity = num(cargo['capacity']) ?? num(currentShip.cargo_capacity);
  const holdValue =
    used !== null || capacity !== null ? `${used ?? '—'} / ${capacity ?? '—'}` : '—';

  const maxGenesis = num(currentShip.max_genesis_devices) ?? 0;
  const loadedGenesis = num(currentShip.genesis_devices) ?? 0;

  return (
    <>
      <MFDPageHeader title="CARGO BAY" accent={ACCENT} status="shipped" showTitle={false} />
      <MFDPageBody scrollKey="cargo">
        <div className="mfd-page-fields">
          <MFDField label="HOLD" value={holdValue} accent />
        </div>

        <div className="mfd-page-section">
          <div className="mfd-page-section-title">MANIFEST</div>
          {items.length > 0 ? (
            <ul className="mfd-page-cargo-list">
              {items.map(([resource, qty]) => (
                <li key={resource} className="mfd-page-cargo-row">
                  <span className="mfd-page-cargo-name">{formatCommodity(resource)}</span>
                  <span className="mfd-page-cargo-qty">× {qty}</span>
                </li>
              ))}
            </ul>
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
