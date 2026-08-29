/**
 * Canon ships.md maintenance band boundaries: 75 / 50 / 25 / 10.
 * Used by ShipSelector condition chrome (LEG-1031).
 */
export function getShipConditionColor(rating: number): string {
  if (rating >= 75) return 'excellent';
  if (rating >= 50) return 'good';
  if (rating >= 25) return 'fair';
  if (rating >= 10) return 'poor';
  return 'critical';
}
