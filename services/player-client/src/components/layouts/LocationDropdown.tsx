import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import CitizenshipBadge from '../governance/CitizenshipBadge';

interface LocationDropdownProps {
  children?: React.ReactNode;
}

/**
 * LocationDropdown — the StatusBar's [◉ location ▾] chip + dropdown shell
 * (WO-UI0-STATUSBAR sub-part a, integrated at the serial mount step). Owns
 * the trigger chip, open/close state, outside-click/Escape dismissal, and
 * now the full location-context header — RELOCATED (not lost) from
 * GameDashboard's three deleted top-left canvas chips (`id="location"`/
 * `"station"`/`"landed"`, the overlap-defect source): sector number/type,
 * region name, `CitizenshipBadge`, and — when docked/landed — the specific
 * station/planet name+type those scene chips used to show.
 * `RegionOwnerControls` (components/governance/, default export, zero
 * required props) mounts as `children` at the integration step:
 *
 *   <LocationDropdown><RegionOwnerControls /></LocationDropdown>
 *
 * The scene-specific docking-target lookups mirror GameDashboard's own
 * `landedPlanet`/`dockedStation` derivation exactly (same fallback order),
 * since this component sits in the persistent shell — an ANCESTOR of
 * GameDashboard's route content — so it can't receive that state as a prop
 * and instead re-derives it from the same `useGame()` fields GameDashboard
 * reads (`planetsInSector`/`stationsInSector`/`playerState`).
 */
const LocationDropdown: React.FC<LocationDropdownProps> = ({ children }) => {
  const { currentSector, playerState, planetsInSector, stationsInSector } = useGame();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  // Pixel a11y REVISE #2 — trigger ref (focus RETURNS here on close) + panel
  // ref (focus MOVES here on open — this is an informational region, not a
  // dialog/menu, so there's no single "first control" to prefer; the panel
  // container itself is the WAI-ARIA-endorsed fallback target).
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Mirrors GameDashboard.tsx's landedPlanet/dockedStation memos exactly
  // (same fallback order) so the relocated readout matches what the old
  // corner chips showed.
  const landedPlanet = useMemo(() => (
    playerState?.is_landed
      ? planetsInSector?.find((p) => p.id === playerState?.current_planet_id) || null
      : null
  ), [playerState?.is_landed, playerState?.current_planet_id, planetsInSector]);

  const dockedStation = useMemo(() => (
    playerState?.is_docked
      ? stationsInSector?.find((s) => s.id === playerState?.current_port_id) ||
        stationsInSector?.[0] || null
      : null
  ), [playerState?.is_docked, playerState?.current_port_id, stationsInSector]);

  // Dismiss on outside click / Escape — this panel sits over the
  // click-through windshield, so a stray click elsewhere must close it
  // rather than leaving a panel stuck open over the scene.
  useEffect(() => {
    if (!open) return;
    const handlePointer = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handlePointer);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  // Pixel a11y REVISE #2 — focus management on the open/close EDGE (ref-
  // tracked previous value, mirrors StatusBar's own wasDossierOpenRef idiom
  // so it never steals focus on initial mount): on open, move focus into
  // the panel; on close (outside click, Escape, or the toggle itself),
  // return it to the trigger.
  const wasOpenRef = useRef(false);
  useEffect(() => {
    if (open && !wasOpenRef.current) {
      panelRef.current?.focus();
    } else if (!open && wasOpenRef.current) {
      triggerRef.current?.focus();
    }
    wasOpenRef.current = open;
  }, [open]);

  const sectorNumber = currentSector?.sector_number || currentSector?.sector_id;
  const sectorLabel = currentSector ? `Sector ${sectorNumber ?? '—'}` : 'Unknown Sector';
  const sectorTypeLabel = currentSector?.type
    ? currentSector.type.replace(/_/g, ' ').toUpperCase()
    : null;
  const regionLabel = currentSector?.region_name;

  return (
    <div className="sb-location" ref={containerRef}>
      <button
        type="button"
        ref={triggerRef}
        className="sb-chip sb-location-chip"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="sb-location-panel"
      >
        <span className="sb-chip-icon" aria-hidden="true">◉</span>
        <span className="sb-location-text">{sectorLabel}</span>
        <span className="sb-chip-caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div
          id="sb-location-panel"
          ref={panelRef}
          tabIndex={-1}
          className="sb-dropdown sb-location-panel"
          role="region"
          aria-label="Location"
        >
          <div className="sb-location-header">
            <div className="sb-location-header-sector">{sectorLabel}</div>
            {sectorTypeLabel && <div className="sb-location-header-type">{sectorTypeLabel}</div>}
            {regionLabel && <div className="sb-location-header-region">{regionLabel}</div>}
            <CitizenshipBadge regionId={currentSector?.region_id} regionName={currentSector?.region_name} />
            {landedPlanet && (
              <div className="sb-location-header-scene">
                🪐 Landed: {landedPlanet.name}
                {landedPlanet.type && ` (${landedPlanet.type.replace(/_/g, ' ').toUpperCase()})`}
              </div>
            )}
            {dockedStation && (
              <div className="sb-location-header-scene">
                🏪 Docked: {dockedStation.name}
                {dockedStation.type && ` (${dockedStation.type.replace(/_/g, ' ').toUpperCase()})`}
              </div>
            )}
          </div>
          {/* RegionOwnerControls mounts here (integration) */}
          <div className="sb-location-body">{children}</div>
        </div>
      )}
    </div>
  );
};

export default LocationDropdown;
