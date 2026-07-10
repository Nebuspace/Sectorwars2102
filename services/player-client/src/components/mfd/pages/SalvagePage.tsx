/**
 * SALVAGE — MFD page (WO-CMB-SALVAGE-LOOP-1 cockpit-ui lane).
 *
 * Lists CargoWrecks in the player's current sector (GET /sectors/{id}/wrecks)
 * and lets the player salvage one via POST /sectors/salvage. Scroll Law: the
 * wreck list is height-capped with its own scroll so the action panel
 * (quantity, turn-cost preview, suspect warning, confirm) stays visible
 * without scrolling once a wreck is selected — the manifest list is the
 * secondary thing allowed to scroll, never the primary control.
 *
 * Turn-cost preview mirrors salvage_service.salvage_wreck's server-side
 * math exactly: ceil(requested_units / 100), 1 turn per 100 units. This is
 * a PREVIEW only — the server independently caps by free cargo hold and
 * available turns and is the sole source of truth for what actually gets
 * charged; the confirm response's turns_spent may differ from the preview
 * if either cap binds tighter than the requested quantity.
 */
import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { sectorAPI, type SectorWreck } from '../../../services/api';
import { MFDPageHeader, MFDPageBody, MFDEmpty, MFDInsufficient } from '../atoms';
import './pages-ship.css';

const ACCENT = '#9EC5FF';

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

// Title-cases regardless of source casing -- destroyed_ship_type comes
// through as an UPPERCASE ShipType enum value (e.g. "CARGO_HAULER"), unlike
// CargoPage's commodity keys (lowercase snake_case); lowercase first so
// both sources land on the same "Cargo Hauler" visual style.
const formatLabel = (key: string): string =>
  key.toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const totalUnits = (cargo: Record<string, number>): number =>
  Object.values(cargo).reduce((sum, qty) => sum + (Number.isFinite(qty) ? qty : 0), 0);

const SalvagePage: React.FC = () => {
  const { currentSector } = useGame();
  const sectorId = currentSector ? currentSector.sector_id : null;

  const [wrecks, setWrecks] = React.useState<SectorWreck[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [quantity, setQuantity] = React.useState<number>(1);
  const [busy, setBusy] = React.useState(false);
  const [salvageMsg, setSalvageMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const refetch = React.useCallback(() => {
    if (sectorId === null) return;
    sectorAPI
      .sectorWrecks(sectorId)
      .then((rows) => {
        setWrecks(rows);
        setLoadError(null);
      })
      .catch((e: any) => {
        setWrecks(null);
        setLoadError(e?.message || 'Failed to load wrecks');
      });
  }, [sectorId]);

  React.useEffect(() => {
    let cancelled = false;
    if (sectorId === null) {
      setWrecks(null);
      return;
    }
    sectorAPI
      .sectorWrecks(sectorId)
      .then((rows) => {
        if (cancelled) return;
        setWrecks(rows);
        setLoadError(null);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setWrecks(null);
        setLoadError(e?.message || 'Failed to load wrecks');
      });
    return () => {
      cancelled = true;
    };
  }, [sectorId]);

  const selected = wrecks?.find((w) => w.id === selectedId) ?? null;

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
      refetch();
    } catch (e: any) {
      // Wreck 404 on a raced expiry (already fully salvaged / cleared by
      // someone else) lands here same as any other salvage failure —
      // refetch so the list reflects reality instead of crashing or
      // showing a stale row the player can no longer act on.
      setSalvageMsg({ ok: false, text: e?.message || 'Salvage failed' });
      setSelectedId(null);
      refetch();
    } finally {
      setBusy(false);
    }
  };

  if (!currentSector) {
    return (
      <>
        <MFDPageHeader title="SALVAGE" accent={ACCENT} status="shipped" />
        <MFDPageBody scrollKey="salvage">
          <MFDInsufficient />
        </MFDPageBody>
      </>
    );
  }

  return (
    <>
      <MFDPageHeader title="SALVAGE" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="salvage">
        <div className="mfd-page-section">
          <div className="mfd-page-section-title">WRECKS IN SECTOR</div>
          {loadError ? (
            <div className="mfd-page-warnline">{loadError}</div>
          ) : wrecks === null ? (
            <MFDEmpty text="LOADING…" />
          ) : wrecks.length === 0 ? (
            <MFDEmpty text="NO WRECKAGE IN THIS SECTOR" />
          ) : (
            <ul className="mfd-page-wreck-list">
              {wrecks.map((wreck) => (
                <li key={wreck.id}>
                  <button
                    type="button"
                    className={`mfd-page-wreck-row${wreck.id === selectedId ? ' selected' : ''}`}
                    onClick={() => selectWreck(wreck)}
                  >
                    <span className="mfd-page-wreck-ship">{formatLabel(wreck.destroyed_ship_type)}</span>
                    <span className="mfd-page-wreck-owner">
                      {wreck.original_owner_name || 'UNKNOWN'}
                    </span>
                    <span className="mfd-page-wreck-age">{formatAge(wreck.age_seconds)}</span>
                    {wreck.would_flag_suspect && <span className="mfd-page-wreck-risk">⚠</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {selected && (
          <div className="mfd-page-section">
            <div className="mfd-page-section-title">
              SALVAGE — {formatLabel(selected.destroyed_ship_type)} ({selected.cause})
            </div>

            <ul className="mfd-page-cargo-list">
              {Object.entries(selected.cargo).map(([commodity, qty]) => (
                <li key={commodity} className="mfd-page-cargo-row">
                  <span className="mfd-page-cargo-name">{formatLabel(commodity)}</span>
                  <span className="mfd-page-cargo-qty">× {qty}</span>
                </li>
              ))}
            </ul>

            <div className="mfd-salvage-row">
              <input
                type="number"
                min={1}
                max={totalUnits(selected.cargo)}
                value={quantity}
                onChange={(e) =>
                  setQuantity(Math.max(1, Math.min(totalUnits(selected.cargo), parseInt(e.target.value, 10) || 1)))
                }
                disabled={busy}
                className="mfd-salvage-input"
              />
              <span className="mfd-salvage-preview">{previewTurns} turn(s)</span>
              <button
                type="button"
                className="mfd-salvage-btn"
                onClick={handleSalvage}
                disabled={busy}
              >
                {busy ? '…' : 'Salvage'}
              </button>
            </div>

            {selected.would_flag_suspect && (
              <div className="mfd-page-warnline">
                ⚠ SALVAGING NOW WILL FLAG YOU SUSPECT — outside the owner's grace window
              </div>
            )}
          </div>
        )}

        {/* Deliberately OUTSIDE the {selected && ...} block: a raced-expiry
            failure clears the selection (the wreck is gone from the
            refetched list), which would otherwise hide this message in the
            same render pass it's meant to explain. */}
        {salvageMsg && (
          <div className={`mfd-mine-msg ${salvageMsg.ok ? 'ok' : 'err'}`}>{salvageMsg.text}</div>
        )}
      </MFDPageBody>
    </>
  );
};

export default React.memo(SalvagePage);
