/** Canon nebula display helpers for NAV 3D chart (LEG-2590 / quantum-resources.md). */

export const NEBULA_STRENGTH_RANGES: Record<string, { min: number; max: number }> = {
  crimson: { min: 80, max: 100 },
  azure: { min: 60, max: 80 },
  emerald: { min: 50, max: 70 },
  violet: { min: 40, max: 60 },
  amber: { min: 20, max: 40 },
  obsidian: { min: 0, max: 20 },
};

/** Mirrors gameserver nebula_color.py cutpoints (builder-proposed, not canon prose). */
export function deriveNebulaTypeFromStrength(strength: number): string {
  if (strength >= 80) return 'crimson';
  if (strength >= 60) return 'azure';
  if (strength >= 50) return 'emerald';
  if (strength >= 40) return 'violet';
  if (strength >= 20) return 'amber';
  return 'obsidian';
}

export function isNebulaSectorType(type: string): boolean {
  return type?.toLowerCase() === 'nebula';
}

/** Hover line e.g. "CRIMSON · 80–100". Returns null when no nebula metadata. */
export function formatNebulaHoverLabel(
  nebula_type?: string | null,
  quantum_field_strength?: number | null,
): string | null {
  if (!nebula_type && quantum_field_strength == null) return null;

  const typeKey = (nebula_type ?? deriveNebulaTypeFromStrength(quantum_field_strength ?? 0)).toLowerCase();
  const range = NEBULA_STRENGTH_RANGES[typeKey];
  if (!range) return nebula_type?.toUpperCase() ?? null;
  return `${typeKey.toUpperCase()} · ${range.min}–${range.max}`;
}
