// @vitest-environment jsdom
/**
 * LEG-4074 Soft-ORDER — CitadelPanel 403/429 densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import { formatCitadelPanelError } from '../CitadelPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatCitadelPanelError 403/429 densify (LEG-4074)', () => {
  it('maps 403/429 without raw transport leakage', () => {
    expect(formatCitadelPanelError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatCitadelPanelError(apiRequestError(403, 'citadel_denied'))).toBe('citadel_denied');
    expect(formatCitadelPanelError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatCitadelPanelError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatCitadelPanelError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
