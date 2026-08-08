import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { decodeToken, isTokenExpired, getTokenTimeRemaining } from './auth';

// Build a syntactically-valid (unsigned-payload) JWT for the given payload.
function makeToken(payload: Record<string, unknown>): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'none', typ: 'JWT' })}.${b64url(payload)}.signature`;
}

describe('decodeToken', () => {
  it('decodes a valid 3-part JWT payload', () => {
    const token = makeToken({ sub: 'user-1', exp: 12345 });
    expect(decodeToken(token)).toEqual({ sub: 'user-1', exp: 12345 });
  });

  it('returns null for a malformed token (wrong part count)', () => {
    expect(decodeToken('not-a-jwt')).toBeNull();
  });

  it('returns null for an empty/falsy token', () => {
    expect(decodeToken('')).toBeNull();
  });

  it('returns null when the payload segment is not valid JSON', () => {
    expect(decodeToken('a.b.c')).toBeNull();
  });
});

describe('isTokenExpired', () => {
  const OLD_DATE_NOW = Date.now;
  beforeEach(() => {
    Date.now = () => new Date('2026-01-01T00:00:00Z').getTime();
  });
  afterEach(() => {
    Date.now = OLD_DATE_NOW;
    localStorage.clear();
  });

  it('returns true when no token is available (arg or localStorage)', () => {
    localStorage.removeItem('accessToken');
    expect(isTokenExpired()).toBe(true);
  });

  it('returns true for a token whose exp is in the past', () => {
    const pastExpSeconds = new Date('2025-01-01T00:00:00Z').getTime() / 1000;
    const token = makeToken({ exp: pastExpSeconds });
    expect(isTokenExpired(token)).toBe(true);
  });

  it('returns false for a token whose exp is in the future', () => {
    const futureExpSeconds = new Date('2027-01-01T00:00:00Z').getTime() / 1000;
    const token = makeToken({ exp: futureExpSeconds });
    expect(isTokenExpired(token)).toBe(false);
  });

  it('returns true when the decoded token has no exp field', () => {
    const token = makeToken({ sub: 'user-1' });
    expect(isTokenExpired(token)).toBe(true);
  });

  it('falls back to localStorage accessToken when no arg is given', () => {
    const futureExpSeconds = new Date('2027-01-01T00:00:00Z').getTime() / 1000;
    localStorage.setItem('accessToken', makeToken({ exp: futureExpSeconds }));
    expect(isTokenExpired()).toBe(false);
  });
});

describe('getTokenTimeRemaining', () => {
  const OLD_DATE_NOW = Date.now;
  beforeEach(() => {
    Date.now = () => new Date('2026-01-01T00:00:00Z').getTime();
  });
  afterEach(() => {
    Date.now = OLD_DATE_NOW;
  });

  it('returns 0 when no token is provided', () => {
    expect(getTokenTimeRemaining('')).toBe(0);
  });

  it('returns 0 for an already-expired token', () => {
    const pastExpSeconds = new Date('2025-01-01T00:00:00Z').getTime() / 1000;
    const token = makeToken({ exp: pastExpSeconds });
    expect(getTokenTimeRemaining(token)).toBe(0);
  });

  it('returns the correct positive remaining seconds for a live token', () => {
    const futureExpSeconds = Date.now() / 1000 + 100;
    const token = makeToken({ exp: futureExpSeconds });
    expect(getTokenTimeRemaining(token)).toBe(100);
  });
});

describe('api axios instance', () => {
  it('attaches the localStorage accessToken as a Bearer auth header via the request interceptor', async () => {
    const { api } = await import('./auth');
    localStorage.setItem('accessToken', 'my-token-123');
    // Drive the registered request interceptor directly against a bare config,
    // mirroring how axios itself invokes it before a request goes out.
    // @ts-expect-error - internal axios interceptor handler access for test purposes
    const handler = api.interceptors.request.handlers[0].fulfilled;
    // @ts-expect-error - a bare {} stands in for AxiosRequestHeaders here
    const result = await handler({ headers: {} });
    expect(result.headers.Authorization).toBe('Bearer my-token-123');
    localStorage.removeItem('accessToken');
  });
});
