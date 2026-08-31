// @vitest-environment jsdom
/**
 * LEG-3094 Soft-ORDER — HarvestYieldPreview TypeError densify.
 */
import { describe, it, expect } from 'vitest';
import { formatHarvestPreviewError } from '../HarvestYieldPreview';

describe('HarvestYieldPreview TypeError densify (LEG-3094)', () => {
  it('formatHarvestPreviewError falls back on TypeError network collapse', () => {
    const text = formatHarvestPreviewError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Yield preview failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves gate copy for known reason keys when not TypeError', () => {
    expect(formatHarvestPreviewError(new Error('no_mining_laser'))).toContain('No mining laser equipped');
  });
});
