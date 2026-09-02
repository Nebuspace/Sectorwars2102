// @vitest-environment jsdom
/**
 * LEG-4073 Soft-ORDER — ProductionPanel 403/429 densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import { formatProductionPanelError } from '../ProductionPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatProductionPanelError 403/429 densify (LEG-4073)', () => {
  it('maps 403/429 without raw transport leakage', () => {
    expect(formatProductionPanelError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatProductionPanelError(apiRequestError(403, 'alloc_denied'))).toBe('alloc_denied');
    expect(formatProductionPanelError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatProductionPanelError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatProductionPanelError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
