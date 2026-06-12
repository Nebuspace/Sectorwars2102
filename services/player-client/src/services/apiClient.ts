/**
 * Shared axios instance with automatic JWT token refresh on 401 responses.
 *
 * All API calls throughout the app should use this instance so that
 * expired-token handling is centralized in one place.
 *
 * Concurrency control uses module-level variables (not React state) to
 * guarantee that multiple simultaneous 401 responses trigger only a
 * single refresh request.  Queued callers wait on the same promise and
 * then retry with the fresh token.
 */
import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

// ---------------------------------------------------------------------------
// Axios instance
// ---------------------------------------------------------------------------

function getBaseURL(): string {
  if (typeof window === 'undefined') return '';

  // Explicit env var takes priority
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }

  // Default: use current origin so requests go through Vite proxy
  return window.location.origin;
}

const apiClient = axios.create({
  baseURL: getBaseURL(),
});

// ---------------------------------------------------------------------------
// Request interceptor – attach the current access token
// ---------------------------------------------------------------------------

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('accessToken');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ---------------------------------------------------------------------------
// Response interceptor – refresh token on 401
// ---------------------------------------------------------------------------

// Module-level state for concurrency control (NOT React state).
let isRefreshing = false;
let refreshPromise: Promise<void> | null = null;

// Extend AxiosRequestConfig to carry our retry flag
interface RetryableRequest extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

async function doRefreshToken(): Promise<void> {
  const storedRefreshToken = localStorage.getItem('refreshToken');
  if (!storedRefreshToken) {
    throw new Error('No refresh token available');
  }

  try {
    console.log('[apiClient] Refreshing access token...');

    // The refresh call must go through a PRISTINE axios instance: apiClient
    // would re-enter this interceptor, and the GLOBAL axios instance carries
    // AuthContext's own 401 response interceptor — routing a dead refresh
    // token through it turns a fast-fail logout into a circular await that
    // silently hangs every queued request.
    const response = await axios.create().post(
      `${getBaseURL()}/api/v1/auth/refresh`,
      { refresh_token: storedRefreshToken },
      { headers: { Authorization: '' } },
    );

    const { access_token, refresh_token } = response.data;

    localStorage.setItem('accessToken', access_token);
    localStorage.setItem('refreshToken', refresh_token);

    // Also update the global axios default header so that any code still
    // using the global axios instance picks up the new token.
    axios.defaults.headers.common['Authorization'] = `Bearer ${access_token}`;

    console.log('[apiClient] Token refresh succeeded');
  } catch (err) {
    console.error('[apiClient] Token refresh failed:', err);

    // Clear stored auth data
    localStorage.removeItem('accessToken');
    localStorage.removeItem('refreshToken');
    localStorage.removeItem('userId');
    axios.defaults.headers.common['Authorization'] = '';

    throw err;
  }
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetryableRequest | undefined;

    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retry
    ) {
      originalRequest._retry = true;

      // Deduplicate: if a refresh is already in-flight, wait for it.
      if (!isRefreshing) {
        isRefreshing = true;
        refreshPromise = doRefreshToken().finally(() => {
          isRefreshing = false;
          refreshPromise = null;
        });
      }

      try {
        // All concurrent callers await the same promise.
        await refreshPromise;

        // Retry the original request with the fresh token.
        const newToken = localStorage.getItem('accessToken');
        if (originalRequest.headers) {
          originalRequest.headers.Authorization = `Bearer ${newToken}`;
        }
        return apiClient(originalRequest);
      } catch (_refreshError) {
        // Refresh failed – redirect to login.
        // Use window.location rather than React Router so this works even
        // outside a React component tree.
        window.location.href = '/';
        return Promise.reject(_refreshError);
      }
    }

    return Promise.reject(error);
  },
);

export default apiClient;
