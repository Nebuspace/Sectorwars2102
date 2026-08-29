import React, { createContext, useState, useContext, useEffect, useRef, ReactNode } from 'react';
import axios from 'axios';
import { refreshAccessToken } from '../services/apiClient';

interface User {
  id: string;
  username: string;
  email?: string;
  is_admin?: boolean;
}

// WO-PUX-WBACK-SURFACE: the returning-player turn-bonus outcome, mirrored
// from gameserver's AuthResponse.welcome_back (schemas/auth.py). Only
// `granted: true` payloads are ever surfaced to the cockpit — a granted:false
// outcome (no bonus due) is discarded at the login() call site below, same
// as `null` (nothing evaluated, e.g. an OAuth login or a bonus-eval failure).
export interface WelcomeBackOutcome {
  granted: boolean;
  bonus: number;
  days_inactive: number;
}

// WO-IL6 / LEG-834: gameserver notice when invite_code was supplied but did not redeem (D10).
const INVITE_REDEMPTION_NOTICE = 'invite_invalid_or_expired';

export function inviteRedemptionNoticeMessage(notice: string | undefined): string | undefined {
  if (notice === INVITE_REDEMPTION_NOTICE) {
    return (
      'That invite link is no longer valid. Your account was created in the default starter region.'
    );
  }
  return notice;
}

function oauthProviderUrl(
  apiBase: string,
  provider: string,
  register: boolean,
  inviteCode?: string,
): string {
  const trimmed = inviteCode?.trim();
  const inviteSuffix = trimmed ? `&invite=${encodeURIComponent(trimmed)}` : '';
  const registerQuery = register ? `?register=true${inviteSuffix}` : '';

  if (
    window.location.hostname.includes('.app.github.dev') ||
    window.location.hostname.includes('github.dev')
  ) {
    const hostname = window.location.hostname;
    const parts = hostname.split('.');
    const hostnamePart = parts[0];
    const lastDashIndex = hostnamePart.lastIndexOf('-');
    const codespaceName =
      lastDashIndex !== -1 ? hostnamePart.substring(0, lastDashIndex) : hostnamePart;

    if (register) {
      return `https://${codespaceName}-8080.app.github.dev/api/v1/auth/${provider}${registerQuery}`;
    }
    return `https://${codespaceName}-8080.app.github.dev/api/v1/auth/${provider}`;
  }

  if (register) {
    return `${apiBase}/api/v1/auth/${provider}${registerQuery}`;
  }
  return `${apiBase}/api/v1/auth/${provider}`;
}

// WO-FIX-MFA-BYPASS-LOGIN-ROUTES: a 200 response with requires_mfa: true (and
// no tokens) means the account has MFA enabled and the login is mid-flow, not
// failed. login() throws this typed error so a consumer (LoginForm) can
// distinguish "show the MFA-code prompt" from "the credentials were wrong"
// and retry login() with mfaCode populated.
export class MFARequiredError extends Error {
  constructor() {
    super('MFA code required');
    this.name = 'MFARequiredError';
  }
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string, mfaCode?: string) => Promise<void>;
  register: (
    username: string,
    email: string,
    password: string,
    inviteCode?: string,
  ) => Promise<string | undefined>;
  loginWithOAuth: (provider: string) => void;
  registerWithOAuth: (provider: string, inviteCode?: string) => void;
  logout: () => void;
  refreshToken: () => Promise<void>;
  // Monotonic counter (mirrors newMessageSignal/medalAwardedSignal in
  // WebSocketContext) bumped exactly once per login that GRANTED a
  // welcome-back bonus; 0 is the mount baseline, never a real grant.
  // AuthContext sits OUTSIDE WebSocketProvider in the tree (WebSocketContext
  // itself consumes useAuth), so it cannot call addNotification directly —
  // consumers mounted inside WebSocketProvider (GameLayout's
  // WelcomeBackToastWiring) watch this signal instead.
  welcomeBackSignal: number;
  lastWelcomeBack: WelcomeBackOutcome | null;
}

const AuthContext = createContext<AuthContextType | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

// Type for response data from login/register endpoints
interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user_id: string;
  welcome_back?: WelcomeBackOutcome | null;
  // WO-FIX-MFA-BYPASS-LOGIN-ROUTES: present (true) with empty tokens when the
  // account has MFA enabled and no/invalid mfa_code was supplied.
  requires_mfa?: boolean;
  [key: string]: any;
}

// Token refresh is delegated to the ONE shared single-flight in
// services/apiClient.ts (refreshAccessToken). The global-axios 401 interceptor
// here and the apiClient interceptor MUST funnel through that single lock —
// two separate locks race on the rotating refresh token (one call rotates it,
// the concurrent one then presents the now-revoked token → 401 → logout).

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }: AuthProviderProps) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [welcomeBackSignal, setWelcomeBackSignal] = useState<number>(0);
  const [lastWelcomeBack, setLastWelcomeBack] = useState<WelcomeBackOutcome | null>(null);

  // WO-PUX-WBACK-SURFACE: bump the signal iff this login just granted a
  // bonus. Read-only — no token/auth-flow side effects, no dedupe state
  // (one-shot semantics are server-guaranteed by welcome_back()'s
  // last_game_login overwrite; a granted:false/null outcome is a no-op here).
  const noteWelcomeBack = (outcome: WelcomeBackOutcome | null | undefined) => {
    if (!outcome?.granted) return;
    setLastWelcomeBack(outcome);
    setWelcomeBackSignal((s) => s + 1);
  };

  // Use Vite proxy for all API requests to avoid CORS issues
  const getApiUrl = () => {
    // If an environment variable is explicitly set, use it
    if (import.meta.env.VITE_API_URL) {
      return import.meta.env.VITE_API_URL;
    }

    // Always use the current origin to leverage Vite proxy in Docker environments
    // This ensures all API calls go through the Vite dev server proxy
    return window.location.origin;
  };
  
  // Initialize axios with API URL - use useMemo-like pattern with ref to avoid recalculation
  const apiUrlRef = useRef<string | null>(null);
  if (apiUrlRef.current === null) {
    apiUrlRef.current = getApiUrl();
  }
  const apiUrl = apiUrlRef.current;

  // Track if auth check has been performed
  const authCheckPerformed = useRef(false);

  // Setup axios interceptor for authentication
  useEffect(() => {
    const interceptor = axios.interceptors.response.use(
      (response) => response,
      async (error) => {
        const originalRequest = error.config;

        // If error is 401 and not already retrying, attempt to refresh token
        if (error.response?.status === 401 && !originalRequest._retry) {
          originalRequest._retry = true;

          try {
            await refreshToken();

            // Re-attempt the original request with new token
            const accessToken = localStorage.getItem('accessToken');
            originalRequest.headers['Authorization'] = `Bearer ${accessToken}`;
            return axios(originalRequest);
          } catch (refreshError) {
            // If refresh token fails, logout
            logout();
            return Promise.reject(refreshError);
          }
        }

        return Promise.reject(error);
      }
    );

    // Check if user is already authenticated - only run once
    const checkAuth = async () => {
      if (authCheckPerformed.current) {
        return;
      }
      authCheckPerformed.current = true;
      setIsLoading(true);

      const accessToken = localStorage.getItem('accessToken');
      if (accessToken) {
        try {
          // Check if the token is already in headers - if not, add it
          if (axios.defaults.headers.common['Authorization'] !== `Bearer ${accessToken}`) {
            axios.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`;
          }

          // Standard approach - verify token by getting user profile
          const response = await axios.get<User>(`${apiUrl}/api/v1/auth/me`);
          setUser(response.data);
        } catch (error) {
          console.error('Failed to validate token:', error);
          
          // Try token refresh before giving up
          try {
            await refreshToken();
            
            // If refresh succeeded, try again to get user data
            const response = await axios.get<User>(`${apiUrl}/api/v1/auth/me`);
            setUser(response.data);
          } catch (refreshError) {
            console.error('Token refresh failed, clearing auth data:', refreshError);
            localStorage.removeItem('accessToken');
            localStorage.removeItem('refreshToken');
            axios.defaults.headers.common['Authorization'] = '';
          }
        }
      }

      setIsLoading(false);
    };
    
    checkAuth();

    // Clean up interceptor
    return () => {
      axios.interceptors.response.eject(interceptor);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run only on mount - apiUrl is stable via ref, authCheckPerformed prevents duplicates
  
  const login = async (username: string, password: string, mfaCode?: string) => {
    setIsLoading(true);

    try {
      // Try standard JSON endpoint
      let response: { data: AuthResponse };
      try {
        response = await axios.post<AuthResponse>(`${apiUrl}/api/v1/auth/login/json`, {
          username,
          password,
          // WO-FIX-MFA-BYPASS-LOGIN-ROUTES: only sent on the MFA-retry call —
          // the schema field is optional and a first-pass login has none yet.
          ...(mfaCode ? { mfa_code: mfaCode } : {}),
        }, {
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          }
        });
      } catch (jsonError) {
        // If JSON login fails, try form-based login as fallback
        const formData = new FormData();
        formData.append('username', username);
        formData.append('password', password);
        if (mfaCode) {
          formData.append('mfa_code', mfaCode);
        }

        response = await axios.post<AuthResponse>(`${apiUrl}/api/v1/auth/login`, formData, {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
        });
      }

      // WO-FIX-MFA-BYPASS-LOGIN-ROUTES: a 200 with requires_mfa: true carries
      // NO tokens — the account has MFA enabled and this login is mid-flow,
      // not failed and not complete. Surface it as a typed error so the
      // caller can show the code-entry step and retry with mfaCode populated
      // instead of silently "succeeding" into an unauthenticated state.
      if (response.data.requires_mfa) {
        throw new MFARequiredError();
      }

      const { access_token, refresh_token } = response.data;
      // Store user ID for future reference
      localStorage.setItem('userId', response.data.user_id);

      // Store tokens in localStorage
      localStorage.setItem('accessToken', access_token);
      localStorage.setItem('refreshToken', refresh_token);

      // Set authorization header
      axios.defaults.headers.common['Authorization'] = `Bearer ${access_token}`;

      noteWelcomeBack(response.data.welcome_back);

      // Get user data
      const userResponse = await axios.get<User>(`${apiUrl}/api/v1/auth/me`);
      setUser(userResponse.data);
    } catch (error) {
      if (!(error instanceof MFARequiredError)) {
        console.error('All login attempts failed:', error);
      }
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const register = async (
    username: string,
    email: string,
    password: string,
    inviteCode?: string,
  ): Promise<string | undefined> => {
    setIsLoading(true);

    try {
      const trimmedInvite = inviteCode?.trim();
      const payload: Record<string, string> = { username, email, password };
      if (trimmedInvite) {
        payload.invite_code = trimmedInvite;
      }

      const { data } = await axios.post<{
        redemption_notice?: string;
      }>(`${apiUrl}/api/v1/auth/register`, payload);

      await login(username, password);

      return inviteRedemptionNoticeMessage(data?.redemption_notice);
    } catch (error) {
      console.error('Registration failed:', error);
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const loginWithOAuth = (provider: string) => {
    sessionStorage.removeItem('oauth_register');
    window.location.href = oauthProviderUrl(apiUrl, provider, false);
  };

  const registerWithOAuth = (provider: string, inviteCode?: string) => {
    sessionStorage.setItem('oauth_register', 'true');
    window.location.href = oauthProviderUrl(apiUrl, provider, true, inviteCode);
  };
  
  const refreshToken = async () => {
    // Delegate to the ONE shared single-flight refresh (services/apiClient.ts):
    // concurrent 401s across the global-axios and apiClient layers coalesce into
    // a single /auth/refresh and all callers receive the same rotated token.
    // refreshAccessToken() already persists the new tokens and updates the global
    // axios Authorization header; it never throws (returns null on failure).
    const token = await refreshAccessToken();
    if (!token) {
      setUser(null);
      throw new Error('Token refresh failed');
    }
  };
  
  const logout = () => {
    const refreshToken = localStorage.getItem('refreshToken');
    
    // Call logout endpoint to invalidate refresh token
    if (refreshToken) {
      axios.post(`${apiUrl}/api/v1/auth/logout`, { refresh_token: refreshToken })
        .catch(error => console.error('Logout error:', error));
    }
    
    // Clear tokens and user
    localStorage.removeItem('accessToken');
    localStorage.removeItem('refreshToken');
    localStorage.removeItem('userId');
    axios.defaults.headers.common['Authorization'] = '';
    setUser(null);
  };
  
  const value = {
    user,
    isAuthenticated: !!user,
    isLoading,
    login,
    register,
    loginWithOAuth,
    registerWithOAuth,
    logout,
    refreshToken,
    welcomeBackSignal,
    lastWelcomeBack,
  };
  
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};