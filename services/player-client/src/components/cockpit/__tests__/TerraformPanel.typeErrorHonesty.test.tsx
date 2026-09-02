// @vitest-environment jsdom
/**
 * LEG-4072 Soft-ORDER — TerraformPanel 403/429 densify (invent=0).
 */
import { describe, expect, it } from 'vitest';
import { formatTerraformPanelError } from '../TerraformPanel';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatTerraformPanelError 403/429 densify (LEG-4072)', () => {
  it('maps 403/429 without raw transport leakage', () => {
    expect(formatTerraformPanelError(apiRequestError(403))).toMatch(/permission/i);
    expect(formatTerraformPanelError(apiRequestError(403, 'terraform_denied'))).toBe('terraform_denied');
    expect(formatTerraformPanelError(apiRequestError(429))).toMatch(/rate limit/i);
    expect(formatTerraformPanelError(apiRequestError(429))).not.toMatch(/\b429\b/);
    expect(formatTerraformPanelError(apiRequestError(403))).not.toMatch(/TypeError/i);
  });
});
