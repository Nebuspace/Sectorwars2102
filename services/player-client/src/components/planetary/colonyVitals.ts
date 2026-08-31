/** Canon 5-level progression (mirrors CitadelManager CITADEL_TRACK / colonization.md). */
const COLONY_PHASES: Record<number, string> = {
  1: 'Outpost',
  2: 'Settlement',
  3: 'Colony',
  4: 'Major Colony',
  5: 'Planetary Capital',
};

/** Lifecycle phase label from citadel level (colonization.md § Colony lifecycle phases). */
export function formatColonyPhase(citadelLevel: number | null | undefined): string {
  const level = typeof citadelLevel === 'number' ? Math.round(citadelLevel) : 0;
  if (level < 1) return 'Unestablished';
  return COLONY_PHASES[level] ?? `Level ${level}`;
}

/** Daily colonist growth from productionRates.colonists (server /day). */
export function formatColonyGrowthPerDay(rate: number | null | undefined): string {
  const n = Number(rate ?? 0);
  if (!Number.isFinite(n) || n === 0) return '0/day';
  const sign = n > 0 ? '+' : '';
  const rounded = Math.abs(n) >= 10 ? Math.round(n) : Math.round(n * 10) / 10;
  return `${sign}${rounded}/day`;
}
