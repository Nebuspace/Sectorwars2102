import { describe, expect, it } from 'vitest';
import { getShipConditionColor } from '../shipConditionColor';

describe('getShipConditionColor (LEG-1031 canon 75/50/25/10)', () => {
  it('maps ≥75 to excellent', () => {
    expect(getShipConditionColor(100)).toBe('excellent');
    expect(getShipConditionColor(75)).toBe('excellent');
  });

  it('maps 50–74 to good', () => {
    expect(getShipConditionColor(74)).toBe('good');
    expect(getShipConditionColor(50)).toBe('good');
  });

  it('maps 25–49 to fair', () => {
    expect(getShipConditionColor(49)).toBe('fair');
    expect(getShipConditionColor(25)).toBe('fair');
  });

  it('maps 10–24 to poor', () => {
    expect(getShipConditionColor(24)).toBe('poor');
    expect(getShipConditionColor(10)).toBe('poor');
  });

  it('maps <10 to critical', () => {
    expect(getShipConditionColor(9)).toBe('critical');
    expect(getShipConditionColor(0)).toBe('critical');
  });
});
