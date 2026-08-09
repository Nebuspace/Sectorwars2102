/**
 * expeditionTypes — snake_case / camelCase field accessors.
 */
import { describe, expect, it } from 'vitest';
import {
  EXPEDITION_DELAY_MINUTES,
  expeditionLaunchedAt,
  expeditionPlanetId,
  siteEnergyBaseline,
  siteNativeLife,
  siteShapeClass,
  siteTemplateId,
  siteUsableSlots,
  type Expedition,
  type SiteIntel,
} from '../expeditionTypes';

describe('expeditionTypes accessors', () => {
  it('exports the display delay constant', () => {
    expect(EXPEDITION_DELAY_MINUTES).toBe(10);
  });

  it('prefers snake_case then falls back to camelCase on SiteIntel', () => {
    const snake: SiteIntel = {
      shape_class: 'crater',
      template_id: 't1',
      usable_slots: 4,
      energy_baseline: 'high',
      native_life: true,
    };
    expect(siteShapeClass(snake)).toBe('crater');
    expect(siteTemplateId(snake)).toBe('t1');
    expect(siteUsableSlots(snake)).toBe(4);
    expect(siteEnergyBaseline(snake)).toBe('high');
    expect(siteNativeLife(snake)).toBe(true);

    const camel: SiteIntel = {
      shapeClass: 'ridge',
      templateId: 't2',
      usableSlots: 2,
      energyBaseline: 'low',
      nativeLife: false,
    };
    expect(siteShapeClass(camel)).toBe('ridge');
    expect(siteTemplateId(camel)).toBe('t2');
    expect(siteUsableSlots(camel)).toBe(2);
    expect(siteEnergyBaseline(camel)).toBe('low');
    expect(siteNativeLife(camel)).toBe(false);
  });

  it('returns undefined for null/empty SiteIntel', () => {
    expect(siteShapeClass(null)).toBeUndefined();
    expect(siteUsableSlots(undefined)).toBeUndefined();
  });

  it('reads expedition planet and launch time from either casing', () => {
    const snake = {
      id: 'e1',
      status: 'SUCCESS',
      planet_id: 'p1',
      launched_at: '2026-01-01T00:00:00Z',
    } as Expedition;
    expect(expeditionPlanetId(snake)).toBe('p1');
    expect(expeditionLaunchedAt(snake)).toBe('2026-01-01T00:00:00Z');

    const camel = {
      id: 'e2',
      status: 'PENDING',
      planetId: 'p2',
      launchedAt: '2026-02-01T00:00:00Z',
    } as Expedition;
    expect(expeditionPlanetId(camel)).toBe('p2');
    expect(expeditionLaunchedAt(camel)).toBe('2026-02-01T00:00:00Z');
  });
});
