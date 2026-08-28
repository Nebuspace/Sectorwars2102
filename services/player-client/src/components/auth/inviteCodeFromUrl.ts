/** Read optional region-invite code from the current page URL (LEG-834). */
export function inviteCodeFromUrl(search = window.location.search): string {
  const params = new URLSearchParams(search);
  return (params.get('invite') ?? params.get('invite_code') ?? '').trim();
}
