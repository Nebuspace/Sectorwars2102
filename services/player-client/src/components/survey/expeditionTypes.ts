// Shared shapes for the survey/expedition UI (ADR-0091 "Planetary Survey &
// Site Discovery"). Mirrors expeditions.py's `_serialize()` — read every
// field defensively (the gameserver payload shape is lane2's to define; we
// degrade gracefully rather than crash the cockpit on an unrecognized field).

export type ExpeditionStatus = 'PENDING' | 'SUCCESS' | 'PARTIAL' | 'FAILURE';

export interface SiteResource {
  tier?: string;
  deposit?: string;
}

export interface SiteHazard {
  kind?: string;
  sev?: number;
}

/** The ephemeral SiteIntel payload (ADR §1) — NULL on FAILURE/PENDING. */
export interface SiteIntel {
  shape_class?: string;
  shapeClass?: string;
  template_id?: string;
  templateId?: string;
  usable_slots?: number;
  usableSlots?: number;
  /** PARTIAL outcomes return only shape/slots — banded/uncertain (ADR §1). */
  banded?: boolean;
  energy_baseline?: string;
  energyBaseline?: string;
  native_energy_present?: boolean;
  nativeEnergyPresent?: boolean;
  hazards?: SiteHazard[];
  resources?: SiteResource[];
  native_life?: boolean | null;
  nativeLife?: boolean | null;
}

export interface Expedition {
  id: string;
  planet_id?: string;
  planetId?: string;
  ship_id?: string | null;
  shipId?: string | null;
  status: ExpeditionStatus;
  result?: SiteIntel | null;
  demo?: boolean;
  launched_at?: string;
  launchedAt?: string;
}

export const siteShapeClass = (s: SiteIntel | null | undefined): string | undefined =>
  s?.shape_class ?? s?.shapeClass;

export const siteTemplateId = (s: SiteIntel | null | undefined): string | undefined =>
  s?.template_id ?? s?.templateId;

export const siteUsableSlots = (s: SiteIntel | null | undefined): number | undefined =>
  s?.usable_slots ?? s?.usableSlots;

export const siteEnergyBaseline = (s: SiteIntel | null | undefined): string | undefined =>
  s?.energy_baseline ?? s?.energyBaseline;

export const siteNativeLife = (s: SiteIntel | null | undefined): boolean | null | undefined =>
  s?.native_life ?? s?.nativeLife;

export const expeditionPlanetId = (e: Expedition): string | undefined =>
  e.planet_id ?? e.planetId;

export const expeditionLaunchedAt = (e: Expedition): string | undefined =>
  e.launched_at ?? e.launchedAt;

// ADR-0091 default; env-tunable server-side. Treated as a display constant
// here — the API does not (yet) surface it, so the countdown is a best-effort
// estimate anchored to launched_at, not a server-pushed remaining-seconds value.
export const EXPEDITION_DELAY_MINUTES = 10;
