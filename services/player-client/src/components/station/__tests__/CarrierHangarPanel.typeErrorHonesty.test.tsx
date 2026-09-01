// @vitest-environment jsdom
/**
 * LEG-3076 Soft-ORDER — CarrierHangarPanel TypeError densify.
 * Hangar actions must not surface raw Failed to fetch / TypeError.
 */
import { describe, it, expect } from 'vitest';
import { formatHangarActionError } from '../CarrierHangarPanel';

describe('CarrierHangarPanel TypeError densify (LEG-3076)', () => {
  it('formatHangarActionError falls back on TypeError network collapse', () => {
    const text = formatHangarActionError(new TypeError('Failed to fetch'));
    expect(text).toMatch(/Hangar action failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('preserves non-generic Error.message detail when not TypeError', () => {
    expect(formatHangarActionError(new Error('dock_request_denied'))).toBe('dock_request_denied');
  });

  it('formatHangarActionError falls back on axios Network Error / Failed to fetch (LEG-3365)', () => {
    expect(formatHangarActionError(new Error('Network Error'))).toBe('Hangar action failed');
    expect(formatHangarActionError(new Error('Failed to fetch'))).toBe('Hangar action failed');
    expect(formatHangarActionError(new Error('Network Error'))).not.toMatch(/Network Error/i);
  });
});
