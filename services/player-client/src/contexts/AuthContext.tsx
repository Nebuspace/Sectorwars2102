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

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  loginWithOAuth: (provider: string) => void;
  registerWithOAuth: (provider: string) => void;
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
  
  const login = async (username: string, password: string) => {
    setIsLoading(true);

    try {
      // Try standard JSON endpoint
      try {
        const response = await axios.post<AuthResponse>(`${apiUrl}/api/v1/auth/login/json`, {
          username,
          password,
        }, {
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          }
        });

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
        return;
      } catch (jsonError) {
        // If JSON login fails, try form-based login as fallback

        // Create form data
        const formData = new FormData();
        formData.append('username', username);
        formData.append('password', password);

        try {
          const response = await axios.post<AuthResponse>(`${apiUrl}/api/v1/auth/login`, formData, {
            headers: {
              'Content-Type': 'multipart/form-data',
            },
          });

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
        } catch (formError) {
          throw formError;
        }
      }
    } catch (error) {
      console.error('All login attempts failed:', error);
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const register = async (username: string, email: string, password: string): Promise<void> => {
    setIsLoading(true);

    try {
      // Register user
      await axios.post<AuthResponse>(`${apiUrl}/api/v1/auth/register`, {
        username,
        email,
        password,
      });

      // After registration, automatically log in
      await login(username, password);
    } catch (error) {
      console.error('Registration failed:', error);
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  const loginWithOAuth = (provider: string) => {
    // Redirect to OAuth provider for login
    // Make sure we don't have a stale registration flag
    sessionStorage.removeItem('oauth_register');

    // For GitHub Codespaces, construct the correct URL directly
    let oauthUrl;
    if (window.location.hostname.includes('.app.github.dev') ||
        window.location.hostname.includes('github.dev')) {
      // Get the codespace name from the hostname
      const hostname = window.location.hostname;

      // Extract the codespace name from the hostname
      // Format is like: super-duper-carnival-qppjvq94q9vcxwqp-3000.app.github.dev
      // We want: super-duper-carnival-qppjvq94q9vcxwqp
      const parts = hostname.split('.');
      const hostnamePart = parts[0]; // e.g., super-duper-carnival-qppjvq94q9vcxwqp-3000
      const lastDashIndex = hostnamePart.lastIndexOf('-');
      const codespaceName = lastDashIndex !== -1 ? hostnamePart.substring(0, lastDashIndex) : hostnamePart;

      // Construct the URL directly to the gameserver port
      oauthUrl = `https://${codespaceName}-8080.app.github.dev/api/v1/auth/${provider}`;
    } else {
      // For non-Codespaces environments
      oauthUrl = `${apiUrl}/api/v1/auth/${provider}`;
    }
    window.location.href = oauthUrl;
  };

  const registerWithOAuth = (provider: string) => {
    // Currently, the backend uses the same endpoint for both login and registration
    // The OAuth provider will handle first-time users as registrations
    // Store in session storage that this was a registration attempt
    sessionStorage.setItem('oauth_register', 'true');

    // For GitHub Codespaces, construct the correct URL directly
    let oauthUrl;
    if (window.location.hostname.includes('.app.github.dev') ||
        window.location.hostname.includes('github.dev')) {
      // Get the codespace name from the hostname
      const hostname = window.location.hostname;

      // Extract the codespace name from the hostname
      const parts = hostname.split('.');
      const hostnamePart = parts[0];
      const lastDashIndex = hostnamePart.lastIndexOf('-');
      const codespaceName = lastDashIndex !== -1 ? hostnamePart.substring(0, lastDashIndex) : hostnamePart;

      // Construct the URL directly to the gameserver port
      oauthUrl = `https://${codespaceName}-8080.app.github.dev/api/v1/auth/${provider}?register=true`;
    } else {
      // For non-Codespaces environments
      oauthUrl = `${apiUrl}/api/v1/auth/${provider}?register=true`;
    }
    window.location.href = oauthUrl;
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