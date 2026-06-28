/**
 * Authentication utility functions
 */

/**
 * Get authentication token from localStorage
 */
export const getAuthToken = (): string | null => {
  return localStorage.getItem('accessToken');
};

// WO-C4: removed the dead `decodeToken` (which had a `mock-` token backdoor that
// minted a fake authenticated payload) + its only callers `isTokenExpired` /
// `getTokenTimeRemaining` — all three had zero live references repo-wide (only
// `getAuthToken` is used, by TerraformingPanel.tsx). Token expiry/validation is
// handled server-side; the client only carries the bearer token.