/**
 * Region-invite codes for signup (LEG-31 / WO-IL6).
 * Mirrors gameserver `_sanitize_oauth_invite` — shareable codes, not secrets.
 */

export const REGION_INVITE_STORAGE_KEY = 'region_invite_code';

const INVITE_RE = /^[A-Za-z0-9_-]+$/;

export function sanitizeOauthInvite(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const invite = raw.trim();
  if (!invite || invite.length > 64) return null;
  if (!INVITE_RE.test(invite)) return null;
  return invite;
}

export function persistRegionInvite(code: string): void {
  sessionStorage.setItem(REGION_INVITE_STORAGE_KEY, code);
}

export function readStoredRegionInvite(): string | null {
  return sanitizeOauthInvite(sessionStorage.getItem(REGION_INVITE_STORAGE_KEY));
}

export function clearStoredRegionInvite(): void {
  sessionStorage.removeItem(REGION_INVITE_STORAGE_KEY);
}

/** Capture `?invite=` from a location search string and persist if valid. */
export function captureInviteFromLocationSearch(search: string): string | null {
  const q = search.startsWith('?') ? search.slice(1) : search;
  const code = sanitizeOauthInvite(new URLSearchParams(q).get('invite'));
  if (code) persistRegionInvite(code);
  return code;
}

export function oauthInviteQuerySuffix(invite: string | null | undefined): string {
  const code = sanitizeOauthInvite(invite);
  return code ? `&invite=${encodeURIComponent(code)}` : '';
}
