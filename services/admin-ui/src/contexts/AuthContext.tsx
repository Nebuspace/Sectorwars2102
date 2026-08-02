import React, { createContext, useState, useContext, useEffect, ReactNode } from 'react';
import axios from 'axios';

export interface User {
  id: string;
  username: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
  last_login: string | null;
  mfaEnabled?: boolean;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<{ requiresMFA: boolean; sessionToken?: string }>;
  verifyMFA: (code: string, sessionToken: string) => Promise<void>;
  logout: () => void;
  refreshToken: () => Promise<void>;
  enableMFA: () => Promise<{ secret: string; qrCodeUrl: string; backupCodes: string[] }>;
  confirmMFASetup: (code: string, secret: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  /**
   * API URL Strategy: Use Relative URLs for Proxy Architecture
   *
   * We use relative URLs (/api/v1/*) instead of absolute URLs to ensure
   * compatibility across all deployment environments:
   *
   * - Development (Codespaces/Local): Vite dev server proxies /api/* to gameserver:8080
   * - Production (Docker): Nginx reverse proxy routes /api/v1/* to gameserver backend
   * - Multi-regional: Nginx handles regional routing transparently
   *
   * This pattern avoids CORS issues and works consistently across environments.
   * See vite.config.ts (lines 38-70) and nginx.conf (lines 157-179) for proxy configs.
   *
   * If future deployments require different API URLs (CDN, mobile apps, etc.),
   * implement environment-based configuration at that time.
   */
  
  // Helper function to clear auth data
  const clearAuthData = () => {
    localStorage.removeItem('accessToken');
    localStorage.removeItem('refreshToken');
    axios.defaults.headers.common['Authorization'] = '';
    setUser(null);
    setToken(null);
  };
  
  // Refresh token function
  const refreshToken = async (): Promise<void> => {
    const refreshTokenStr = localStorage.getItem('refreshToken');
    if (!refreshTokenStr) {
      console.error('No refresh token available in localStorage');
      throw new Error('No refresh token available');
    }
    
    try {
      console.log('Attempting to refresh token with refresh_token:', refreshTokenStr.substring(0, 5) + '...');
      
      // Use relative URL to go through Vite proxy
      const apiPath = '/api/v1';
      
      const response = await fetch(
        `${apiPath}/auth/refresh`,
        { 
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          },
          body: JSON.stringify({ refresh_token: refreshTokenStr })
        }
      );
      
      // Check if the response is successful first
      if (!response.ok) {
        // If we get an error response, throw an error with the status code
        throw new Error(`Server returned status ${response.status}`);
      }
      
      // Get response text for debugging
      const responseText = await response.text();
      console.log('Raw refresh token response:', responseText);
      
      if (!responseText || responseText.trim() === '') {
        console.error('Empty response from refresh endpoint');
        throw new Error('Empty response from server');
      }
      
      // Parse the response manually
      let data;
      try {
        data = JSON.parse(responseText);
        console.log('Parsed refresh token data:', data);
      } catch (parseError) {
        console.error('Failed to parse refresh token response:', parseError);
        throw new Error(`Invalid JSON response: ${responseText.substring(0, 50)}...`);
      }
      
      // Destructure with default empty values to prevent undefined errors
      const { access_token = '', refresh_token = '' } = data || {};
      
      if (!access_token || !refresh_token) {
        console.error('Missing tokens in refresh response:', data);
        throw new Error(
          `Incomplete token data: Access token=${!!access_token}, Refresh token=${!!refresh_token}`
        );
      }
      
      // Store new tokens
      localStorage.setItem('accessToken', access_token);
      localStorage.setItem('refreshToken', refresh_token);
      setToken(access_token);
      
      // Update auth header for axios requests
      axios.defaults.headers.common['Authorization'] = `Bearer ${access_token}`;
    } catch (error) {
      console.error('Token refresh failed:', error);
      // Clear tokens and user on refresh failure
      clearAuthData();
      throw error;
    }
  };
  
  // Setup authentication check and interceptor
  useEffect(() => {
    // Set up timeout to prevent infinite loading state
    const authTimeout = setTimeout(() => {
      console.warn('Authentication check timed out');
      clearAuthData();
      setIsLoading(false);
    }, 5000);
    
    // Set up axios interceptor for authentication
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
            clearAuthData();
            return Promise.reject(refreshError);
          }
        }
        
        return Promise.reject(error);
      }
    );
    
    // Check if user is already authenticated
    const checkAuth = async () => {
      const accessToken = localStorage.getItem('accessToken');
      if (!accessToken) {
        clearAuthData();
        setIsLoading(false);
        clearTimeout(authTimeout);
        return;
      }
      
      try {
        // Add token to default headers
        axios.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`;
        setToken(accessToken);
        
        // Try to get user profile
        const response = await axios.get<User>('/api/v1/auth/me');
        setUser(response.data);
        console.log('Auth successful - user loaded:', response.data);
      } catch (error: any) {
        // Check if it's a rate limit error (429)
        if (error?.response?.status === 429) {
          console.warn('Rate limit hit during auth check - will retry in a moment');
          // Don't clear auth data on rate limit errors
          setIsLoading(false);
          return;
        }
        
        console.error('Initial auth check failed:', error);
        
        // Try token refresh if initial check fails (but not for rate limits)
        if (error?.response?.status !== 429) {
          try {
            await refreshToken();
            const userResponse = await axios.get<User>('/api/v1/auth/me');
            setUser(userResponse.data);
            console.log('Auth successful after refresh');
          } catch (refreshError: any) {
            if (refreshError?.response?.status === 429) {
              console.warn('Rate limit hit during token refresh');
            } else {
              console.error('Refresh token failed:', refreshError);
              clearAuthData();
            }
          }
        }
      } finally {
        clearTimeout(authTimeout);
        setIsLoading(false);
      }
    };
    
    // Start auth check
    checkAuth();
    
    // Clean up
    return () => {
      clearTimeout(authTimeout);
      axios.interceptors.response.eject(interceptor);
    };
  }, []);
  
  // Login function with MFA support
  const login = async (username: string, password: string): Promise<{ requiresMFA: boolean; sessionToken?: string }> => {
    setIsLoading(true);
    
    try {
      console.log('Attempting login with username:', username);
      
      // Use relative URL to go through Vite proxy
      const apiPath = '/api/v1';
      console.log('Using API via Vite proxy');
      
      try {
        console.log('Attempting direct login call to gameserver API');
        
        // Use fetch API instead of axios for better control
        const directResponse = await fetch(`${apiPath}/auth/login/direct`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
          },
          body: JSON.stringify({
            username,
            password,
          })
        });
        
        // Check if the response is successful
        if (!directResponse.ok) {
          // Handle HTTP error status
          if (directResponse.status === 401) {
            throw new Error('Invalid username or password');
          } else {
            const errorText = await directResponse.text();
            throw new Error(`Server error (${directResponse.status}): ${errorText}`);
          }
        }
        
        // Get response text first for debugging
        const responseText = await directResponse.text();
        console.log('Raw API response:', responseText);
        
        if (!responseText || responseText.trim() === '') {
          throw new Error('Empty response from server');
        }
        
        // Parse the response manually
        let data;
        try {
          data = JSON.parse(responseText);
          console.log('Parsed login data:', data);
        } catch (parseError) {
          console.error('Failed to parse login response:', parseError);
          throw new Error(`Invalid JSON response: ${responseText.substring(0, 50)}...`);
        }
        
        // Check if MFA is required
        if (data.requires_mfa && data.session_token) {
          console.log('MFA required for user');
          // The session token is returned to the caller (LoginForm owns the
          // authoritative copy) rather than mirrored into provider state.
          return { requiresMFA: true, sessionToken: data.session_token };
        }

        // Destructure with default empty values to prevent undefined errors
        const { access_token = '', refresh_token = '', user_id = '' } = data || {};

        if (!access_token || !refresh_token) {
          console.error('Missing tokens in login response:', data);
          throw new Error(
            `Incomplete token data: Access token=${!!access_token}, Refresh token=${!!refresh_token}`
          );
        }

        // Store tokens in localStorage
        localStorage.setItem('accessToken', access_token);
        localStorage.setItem('refreshToken', refresh_token);
        setToken(access_token);

        // Set authorization header for future axios requests
        axios.defaults.headers.common['Authorization'] = `Bearer ${access_token}`;

        // Get user data
        console.log('Fetching user data from /auth/me');
        try {
          const userResponse = await fetch(`${apiPath}/auth/me`, {
            headers: {
              'Authorization': `Bearer ${access_token}`,
              'Content-Type': 'application/json',
            }
          });
          
          if (!userResponse.ok) {
            console.warn(`User data fetch failed with status ${userResponse.status}`);
            throw new Error(`Failed to fetch user data: ${userResponse.status}`);
          }
          
          const userData = await userResponse.json();
          setUser(userData);
          console.log('Login successful with direct login endpoint');
        } catch (userError) {
          console.error('Failed to fetch user data, but login was successful:', userError);
          // Fetch user data failed, but login succeeded
          // We could still try to extract user_id from the token if needed
          if (user_id) {
            setUser({
              id: user_id,
              username: username,
              email: '',
              is_admin: false,
              is_active: true,
              last_login: null
            });
          }
        }
        return { requiresMFA: false };
      } catch (error) {
        console.error('Login attempt failed:', error);
        throw error;
      }
    } catch (error) {
      console.error('All login attempts failed:', error);
      clearAuthData();
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // MFA verification function
  const verifyMFA = async (code: string, sessionToken: string) => {
    setIsLoading(true);
    
    try {
      const apiPath = '/api/v1';
      
      const response = await fetch(`${apiPath}/auth/mfa/verify`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({
          code,
          session_token: sessionToken
        })
      });
      
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Invalid MFA code');
        } else {
          const errorText = await response.text();
          throw new Error(`Server error (${response.status}): ${errorText}`);
        }
      }
      
      const data = await response.json();
      
      // Store tokens
      localStorage.setItem('accessToken', data.access_token);
      localStorage.setItem('refreshToken', data.refresh_token);
      setToken(data.access_token);
      axios.defaults.headers.common['Authorization'] = `Bearer ${data.access_token}`;
      
      // Get user data
      const userResponse = await fetch(`${apiPath}/auth/me`, {
        headers: {
          'Authorization': `Bearer ${data.access_token}`,
          'Content-Type': 'application/json',
        }
      });
      
      if (userResponse.ok) {
        const userData = await userResponse.json();
        setUser(userData);
      }
    } catch (error) {
      console.error('MFA verification failed:', error);
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Enable MFA for current user
  const enableMFA = async () => {
    const apiPath = '/api/v1';
    
    const response = await fetch(`${apiPath}/auth/mfa/generate`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      }
    });
    
    if (!response.ok) {
      throw new Error('Failed to generate MFA secret');
    }
    
    const data = await response.json();
    return {
      secret: data.secret,
      qrCodeUrl: data.qr_code_url,
      backupCodes: data.backup_codes
    };
  };
  
  // Confirm MFA setup
  const confirmMFASetup = async (code: string, secret: string) => {
    const apiPath = '/api/v1';
    
    const response = await fetch(`${apiPath}/auth/mfa/confirm`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        code,
        secret
      })
    });
    
    if (!response.ok) {
      if (response.status === 400) {
        throw new Error('Invalid MFA code');
      }
      throw new Error('Failed to confirm MFA setup');
    }
    
    // Update user to reflect MFA is enabled
    if (user) {
      setUser({ ...user, mfaEnabled: true });
    }
  };
  
  // Logout function
  const logout = () => {
    const refreshTokenStr = localStorage.getItem('refreshToken');

    // Clear tokens and user immediately
    clearAuthData();

    // Call logout endpoint to invalidate refresh token, but don't wait for it
    if (refreshTokenStr) {
      // Set a short timeout to avoid CORS issues during page navigation
      axios.post('/api/v1/auth/logout', { refresh_token: refreshTokenStr }, {
        timeout: 1000 // 1 second timeout
      }).catch(() => {
        // Just log the error, don't block logout
        console.log('Logout token invalidation skipped');
      });
    }

    // Redirect to login page
    window.location.href = '/login';
  };
  
  // Context value
  const value = {
    user,
    token,
    isAuthenticated: !!user,
    isLoading,
    login,
    verifyMFA,
    logout,
    refreshToken,
    enableMFA,
    confirmMFASetup,
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