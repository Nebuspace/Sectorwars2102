import React from 'react';
import { sectorAPI, type SectorWreck } from '../../../services/api';
import './solar-salvage.css';

/**
 * SolarSalvagePage — SOLAR SYSTEM monitor's SALVAGE page (WO-UI2-DECK-
 * RECONCILE, §05: "SALVAGE: wreck rows → SALVAGE ▸").
 *
 * Logic ported from mfd/pages/SalvagePage.tsx (left untouched, read-only
 * source — a later cleanup WO retires the now-unreachable MFD SALV page
 * per the design brief's own rollup table), re-laid-out for the deck-
 * monitor's screen-hud-content shape instead of MFDPageHeader/MFDPageBody
 * chrome. One difference from the MFD source: the wreck LIST is sourced
 * from GameDashboard's existing `sectorWrecks` state (already fetched for
 * the windshield's SCAN layer, WO-UI2-LIVING-WINDSHIELD) via props,
 * instead of an independent GET — one fetch per sector, not two. The
 * SALVAGE action itself (POST /sectors/salvage) is still made directly
 * here; `onSalvaged` tells the parent to refetch the shared list.
 */

interface SolarSalvagePageProps {
  wrecks: SectorWreck[];
  onSalvaged: () => void;
}

const formatAge = (totalSeconds: number): string => {
  const s = Math.max(0, Math.floor(totalSeconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
};

const formatLabel = (key: string): string =>
  key.toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const totalUnits = (cargo: Record<string, number>): number =>
  Object.values(cargo).reduce((sum, qty) => sum + (Number.isFinite(qty) ? qty : 0), 0);

const SolarSalvagePage: React.FC<SolarSalvagePageProps> = ({ wrecks, onSalvaged }) => {
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [quantity, setQuantity] = React.useState<number>(1);
  const [busy, setBusy] = React.useState(false);
  const [salvageMsg, setSalvageMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const selected = wrecks.find((w) => w.id === selectedId) ?? null;

  const selectWreck = (wreck: SectorWreck) => {
    setSelectedId(wreck.id);
    setQuantity(Math.max(1, totalUnits(wreck.cargo)));
    setSalvageMsg(null);
  };

  const previewTurns = Math.ceil(Math.max(0, quantity) / 100);

  const handleSalvage = async () => {
    if (!selected || busy || quantity < 1) return;
    setBusy(true);
    setSalvageMsg(null);
    try {
      const result = await sectorAPI.salvageWreck(selected.id, quantity);
      const units = totalUnits(result.salvaged);
      setSalvageMsg({
        ok: true,
        text: `Salvaged ${units} unit(s) — ${result.turns_spent} turn(s)${result.suspect_flagged ? ' — SUSPECT flagged' : ''}.`,
      });
      setSelectedId(null);
      onSalvaged();
    } catch (e: any) {
      // Wreck 404 on a raced expiry lands here same as any other salvage
      // failure — refetch so the list reflects reality instead of a stale
      // row the player can no longer act on.
      setSalvageMsg({ ok: false, text: e?.message || 'Salvage failed' });
      setSelectedId(null);
      onSalvaged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {wrecks.length === 0 ? (
        <div className="empty-state">No wreckage in this sector</div>
      ) : (
        <ul className="solar-salvage-wreck-list">
          {wrecks.map((wreck) => (
            <li key={wreck.id}>
              <button
                type="button"
                className={`solar-salvage-wreck-row${wreck.id === selectedId ? ' selected' : ''}`}
                onClick={() => selectWreck(wreck)}
              >
                <span className="solar-salvage-wreck-ship">{formatLabel(wreck.destroyed_ship_type)}</span>
                <span className="solar-salvage-wreck-owner">{wreck.original_owner_name || 'UNKNOWN'}</span>
                <span className="solar-salvage-wreck-age">{formatAge(wreck.age_seconds)}</span>
                {wreck.would_flag_suspect && <span className="solar-salvage-wreck-risk" title="Salvaging now flags you SUSPECT">⚠</span>}
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <div className="solar-salvage-detail">
          <div className="solar-salvage-detail-title">
            SALVAGE — {formatLabel(selected.destroyed_ship_type)} ({selected.cause})
          </div>

          <ul className="solar-salvage-cargo-list">
            {Object.entries(selected.cargo).map(([commodity, qty]) => (
              <li key={commodity} className="solar-salvage-cargo-row">
                <span>{formatLabel(commodity)}</span>
                <span>× {qty}</span>
              </li>
            ))}
          </ul>

          <div className="solar-salvage-row">
            <input
              type="number"
              min={1}
              max={totalUnits(selected.cargo)}
              value={quantity}
              onChange={(e) =>
                setQuantity(Math.max(1, Math.min(totalUnits(selected.cargo), parseInt(e.target.value, 10) || 1)))
              }
              disabled={busy}
              className="solar-salvage-input"
              aria-label="Salvage quantity"
            />
            <span className="solar-salvage-preview">{previewTurns} turn(s)</span>
            <button type="button" className="solar-salvage-btn" onClick={handleSalvage} disabled={busy}>
              {busy ? '…' : 'SALVAGE ▸'}
            </button>
          </div>

          {selected.would_flag_suspect && (
            <div className="solar-salvage-warnline">
              ⚠ SALVAGING NOW WILL FLAG YOU SUSPECT — outside the owner's grace window
            </div>
          )}
        </div>
      )}

      {/* Deliberately OUTSIDE the {selected && ...} block: a raced-expiry
          failure clears the selection in the same render pass, which would
          otherwise hide this message right when it's meant to explain. */}
      {salvageMsg && (
        <div className={`solar-salvage-msg ${salvageMsg.ok ? 'ok' : 'err'}`}>{salvageMsg.text}</div>
      )}
    </>
  );
};

export default SolarSalvagePage;
