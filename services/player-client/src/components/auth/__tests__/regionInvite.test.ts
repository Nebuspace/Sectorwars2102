// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  captureInviteFromLocationSearch,
  oauthInviteQuerySuffix,
  sanitizeOauthInvite,
} from '../regionInvite';

describe('sanitizeOauthInvite', () => {
  it('accepts token-urlsafe-shaped codes', () => {
    expect(sanitizeOauthInvite('AbC_12-xy')).toBe('AbC_12-xy');
  });

  it('drops XSS / query injection', () => {
    expect(sanitizeOauthInvite('<script>')).toBeNull();
    expect(sanitizeOauthInvite('a&b=1')).toBeNull();
    expect(sanitizeOauthInvite('x'.repeat(65))).toBeNull();
  });

  it('oauth suffix matches GS invite query', () => {
    expect(oauthInviteQuerySuffix('AbC_12-xy')).toBe('&invite=AbC_12-xy');
    expect(oauthInviteQuerySuffix('bad space')).toBe('');
  });

  it('captureInviteFromLocationSearch persists valid codes', () => {
    sessionStorage.clear();
    expect(captureInviteFromLocationSearch('?invite=JoinMe_1')).toBe('JoinMe_1');
    expect(sessionStorage.getItem('region_invite_code')).toBe('JoinMe_1');
  });
});
