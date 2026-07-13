import { useState, useEffect, lazy, Suspense } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import axios from 'axios'
import './App.css'

// Import contexts
import { AuthProvider } from './contexts/AuthContext'
import { GameProvider } from './contexts/GameContext'
import { FirstLoginProvider } from './contexts/FirstLoginContext'
import { AutopilotProvider } from './contexts/AutopilotContext'
import { WebSocketProvider } from './contexts/WebSocketContext'
import { SettingsProvider } from './contexts/SettingsContext'
import { ThemeProvider } from './themes/ThemeProvider'

// Import components
import LoginForm from './components/auth/LoginForm'
import RegisterForm from './components/auth/RegisterForm'
import UserProfile from './components/auth/UserProfile'
import OAuthCallback from './components/auth/OAuthCallback'
import LandingPage from './components/landing/LandingPage'
import GameShellRoute from './components/layouts/GameShellRoute'
import GameDashboard from './components/pages/GameDashboard'
import DebugPage from './components/pages/DebugPage'
import TestAuthPage from './components/pages/TestAuthPage'
import { FirstLoginContainer } from './components/first-login'
// WO-UI5-RETIREMENT+GLASS: the 9 other /game/* page components RouteRail's
// nav keys used to route to (map/player/settings/planets/combat/team/
// governance/ranking/ships/trading) are retired to client-side redirects
// below — GameRouteRedirects.tsx. The page components themselves stay on
// disk, untouched (the codebase's established retirement convention — see
// mfdRegistry.tsx's own doc-comment for the same pattern), just no longer
// imported/mounted here.
import { RedirectToGame, RedirectToTacticalTarget } from './components/layouts/GameRouteRedirects'

// Dev-only lab routes — dead-code-eliminated from prod builds by Vite
// Both imports are gated on import.meta.env.DEV so Vite dead-code-eliminates
// the VistaLab and VistaProof chunks from prod builds entirely.
const VistaLab    = import.meta.env.DEV ? lazy(() => import('./vista/lab/VistaLab'))    : null;
const VistaProof  = import.meta.env.DEV ? lazy(() => import('./vista/lab/VistaProof'))  : null;
const VistaParity = import.meta.env.DEV ? lazy(() => import('./vista/lab/VistaParity')) : null;
// WO-UI0-PERSISTENT-SHELL lane B — dev-only geometry harness, same
// DEV-gated dead-code-elimination as the Vista lab routes above.
const LabShell    = import.meta.env.DEV ? lazy(() => import('./components/layouts/LabShell')) : null;

interface ApiResponse {
  message?: string;
  environment?: string;
  version?: string;
  ping?: number;
}

function MainApp() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [user, setUser] = useState<any>(null);
  const [apiStatus, setApiStatus] = useState<string>('Loading...');
  const [apiMessage, setApiMessage] = useState<string>('');
  const [apiEnvironment, setApiEnvironment] = useState<string>('');
  const [authMode, setAuthMode] = useState<'none' | 'login' | 'register'>('none');
  const navigate = useNavigate();

  // Static game feature highlights
  const gameFeatures = [
    { id: 1, type: 'trade', message: 'Real-time multiplayer trading across 5,000+ sectors' },
    { id: 2, type: 'ai', message: 'AI-powered companion ARIA learns your trading style' },
    { id: 3, type: 'warp', message: 'Create planets with Genesis Devices' },
    { id: 4, type: 'combat', message: 'Fleet combat with formation bonuses' },
    { id: 5, type: 'join', message: '18-rank military progression system' },
  ];
  
  // API URL: explicit env override, else same-origin (the Vite proxy and
  // nginx gateway both route /api to the gameserver in every tier, so the
  // page origin always works — localhost:8080 only worked on the dev box).
  const getApiUrl = () => {
    // In GitHub Codespaces, always use the Vite proxy (current origin)
    const isCodespaces = window.location.hostname.includes('.app.github.dev');
    if (isCodespaces) {
      return window.location.origin;
    }
    return import.meta.env.VITE_API_URL || window.location.origin;
  };

  useEffect(() => {
    // Check for auth parameter in URL (coming from OAuth)
    const params = new URLSearchParams(window.location.search);
    const authParam = params.get('auth');
    if (authParam) {
      try {
        const authData = JSON.parse(decodeURIComponent(authParam));
        
        // Store tokens in localStorage
        if (authData.accessToken) {
          localStorage.setItem('accessToken', authData.accessToken);
          localStorage.setItem('refreshToken', authData.refreshToken);
          localStorage.setItem('userId', authData.userId);
          
          // Set axios auth header
          axios.defaults.headers.common['Authorization'] = `Bearer ${authData.accessToken}`;
          
          // Remove the auth parameter from URL to avoid exposing tokens
          const url = new URL(window.location.href);
          url.searchParams.delete('auth');
          window.history.replaceState({}, document.title, url.href);
        }
      } catch (error) {
        console.error('Auth parameter parsing failed');
      }
    }
    
    const apiUrl = getApiUrl();

    const checkApiStatus = async () => {
      try {
        const response = await axios.get(`${apiUrl}/api/v1/status`, {
          timeout: 5000
        });

        if (response.status === 200 && response.data) {
          setApiStatus('Online');
          setApiMessage(response.data.message || 'Game server operational');
          setApiEnvironment(response.data.environment || 'production');
        }
      } catch (error) {
        console.error('API status check failed');
        setApiStatus('Offline');
        setApiMessage('Unable to connect to game server');
        setApiEnvironment('');
      }
    }

    checkApiStatus()

    // Set up interval to check API status every 30 seconds
    const intervalId = setInterval(checkApiStatus, 30000)
    
    // Check if user is authenticated
    const checkAuth = async () => {
      const accessToken = localStorage.getItem('accessToken');
      const isFromOAuth = sessionStorage.getItem('oauth_redirect_completed') === 'true';
      
      if (accessToken) {
        try {
          // Set auth header
          axios.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`;

          // Get user info
          const response = await axios.get(`${apiUrl}/api/v1/auth/me`);
          setUser(response.data);
          setIsAuthenticated(true);
          
          // Clear the OAuth flag if it exists
          if (isFromOAuth) {
            sessionStorage.removeItem('oauth_redirect_completed');
          }
        } catch (error) {
          console.error('Failed to verify authentication');
          // Clear tokens on auth failure
          localStorage.removeItem('accessToken');
          localStorage.removeItem('refreshToken');
          localStorage.removeItem('userId');
          setIsAuthenticated(false);
        }
      }
    };
    
    checkAuth();

    // Clean up interval on component unmount
    return () => clearInterval(intervalId)
  }, [])


  const handleLoginClick = () => {
    setAuthMode('login');
  };

  const handleRegisterClick = () => {
    setAuthMode('register');
  };

  const handleBackToHome = () => {
    setAuthMode('none');
  };

  const handleLogout = () => {
    // Clear tokens
    localStorage.removeItem('accessToken');
    localStorage.removeItem('refreshToken');
    localStorage.removeItem('userId');
    
    // Clear auth header
    axios.defaults.headers.common['Authorization'] = '';
    
    // Update state
    setUser(null);
    setIsAuthenticated(false);
    
    // Redirect if needed
    navigate('/');
  };
  
  if (isAuthenticated) {
    // Redirect to game dashboard
    return <Navigate to="/game" replace />;
  }
  
  return (
    <>
        {authMode === 'login' ? (
          <LoginForm
            onLoginSuccess={() => setAuthMode('none')}
            switchToRegister={() => setAuthMode('register')}
            onClose={() => setAuthMode('none')}
          />
        ) : authMode === 'register' ? (
          <RegisterForm
            onRegisterSuccess={() => setAuthMode('none')}
            switchToLogin={() => setAuthMode('login')}
            onClose={() => setAuthMode('none')}
          />
        ) : (
          <LandingPage onLogin={handleLoginClick} onRegister={handleRegisterClick} />
        )}
    </>
  )
}

// Protected route component
const ProtectedRoute = ({ children }: { children: React.ReactNode }) => {
  const accessToken = localStorage.getItem('accessToken');
  const isAuthenticated = !!accessToken;
  
  if (!isAuthenticated) {
    return <Navigate to="/" replace />;
  }
  
  // Ensure the token is set in axios headers
  if (accessToken && !axios.defaults.headers.common['Authorization']) {
    axios.defaults.headers.common['Authorization'] = `Bearer ${accessToken}`;
  }
  
  return <>{children}</>;
};

function App() {
  return (
    <SettingsProvider>
    <ThemeProvider defaultTheme="cockpit">
      <Router>
        <AuthProvider>
          <WebSocketProvider>
            <GameProvider>
              {/* AutopilotProvider must sit ABOVE the route tree: GameDashboard
                  calls useAutopilot in its own body and renders GameLayout as
                  its wrapper, so a provider inside GameLayout can never cover
                  it. Inside GameProvider (consumes moveToSector). */}
              <AutopilotProvider>
              <FirstLoginProvider>
                <Routes>
              <Route path="/oauth-callback" element={<OAuthCallback />} />
              <Route path="/debug" element={<DebugPage />} />
              <Route path="/test-auth" element={<TestAuthPage />} />
              {/* WO-UI0-PERSISTENT-SHELL lane A — all /game/* pages nest under ONE
                  layout route so GameShellRoute -> GameLayout mounts once and
                  survives navigation between them (only the Outlet slot swaps).
                  ProtectedRoute hoisted to the parent; children inherit the guard. */}
              <Route path="/game" element={
                <ProtectedRoute>
                  <GameShellRoute />
                </ProtectedRoute>
              }>
                <Route index element={<GameDashboard />} />
                {/* WO-UI5-RETIREMENT+GLASS — legacy RouteRail URLs, redirected
                    client-side onto their shipped homes on the single-page
                    cockpit. See GameRouteRedirects.tsx for the deep-link
                    rationale (only /game/combat gets one, via the existing
                    deckNavBus) and the full per-route target map. */}
                <Route path="map" element={<RedirectToGame />} />
                <Route path="team" element={<RedirectToGame />} />
                <Route path="governance" element={<RedirectToGame />} />
                <Route path="combat" element={<RedirectToTacticalTarget />} />
                <Route path="planets" element={<RedirectToGame />} />
                <Route path="ships" element={<RedirectToGame />} />
                <Route path="player" element={<RedirectToGame />} />
                <Route path="trading" element={<RedirectToGame />} />
                <Route path="ranking" element={<RedirectToGame />} />
                <Route path="settings" element={<RedirectToGame />} />
              </Route>
              {import.meta.env.DEV && VistaLab && (
                <Route path="/lab/vista" element={<Suspense fallback={<div>Loading Vista Lab…</div>}><VistaLab /></Suspense>} />
              )}
              {import.meta.env.DEV && VistaProof && (
                <Route path="/lab/vista-proof" element={<Suspense fallback={<div>Loading…</div>}><VistaProof /></Suspense>} />
              )}
              {import.meta.env.DEV && VistaParity && (
                <Route path="/lab/vista-parity" element={<Suspense fallback={<div>Loading…</div>}><VistaParity /></Suspense>} />
              )}
              {import.meta.env.DEV && LabShell && (
                <Route path="/lab/shell" element={<Suspense fallback={<div>Loading…</div>}><LabShell /></Suspense>} />
              )}
              <Route path="*" element={<MainApp />} />
                </Routes>
                <FirstLoginContainer />
              </FirstLoginProvider>
              </AutopilotProvider>
            </GameProvider>
          </WebSocketProvider>
        </AuthProvider>
      </Router>
    </ThemeProvider>
    </SettingsProvider>
  );
}

export default App