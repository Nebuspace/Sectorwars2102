import { useState, useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import axios from 'axios'
import './App.css'

// Import contexts
import { AuthProvider } from './contexts/AuthContext'
import { GameProvider } from './contexts/GameContext'
import { FirstLoginProvider } from './contexts/FirstLoginContext'
import { AutopilotProvider } from './contexts/AutopilotContext'
import { WebSocketProvider } from './contexts/WebSocketContext'
import { ThemeProvider } from './themes/ThemeProvider'

// Import components
import LoginForm from './components/auth/LoginForm'
import RegisterForm from './components/auth/RegisterForm'
import UserProfile from './components/auth/UserProfile'
import OAuthCallback from './components/auth/OAuthCallback'
import GameDashboard from './components/pages/GameDashboard'
import GalaxyMap from './components/pages/GalaxyMap'
import RankingPage from './components/pages/RankingPage'
import DebugPage from './components/pages/DebugPage'
import TestAuthPage from './components/pages/TestAuthPage'
import { FirstLoginContainer } from './components/first-login'

// Import game feature components
import { TeamManager } from './components/teams'
import { CombatInterface } from './components/combat'
import { PlanetManager } from './components/planetary'
import { ShipSelector } from './components/ships'
import { TradingInterface } from './components/trading'

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
    <div className="container">
      <header className="site-header">
        <div className="header-content">
          <div className="logo">
            <h1>Sector Wars 2102</h1>
            <p className="subtitle">The Future of Space Trading</p>
          </div>
          <div className="header-actions">
            <div className="status-indicator-header" title={`Server: ${apiStatus} - ${apiMessage}`}>
              <span className={`status-dot ${apiStatus === 'Online' ? 'online' : 'offline'}`}></span>
              <span className="status-text-compact">{apiStatus}</span>
            </div>
            {!isAuthenticated && (
              <>
                <button className="header-btn" onClick={handleLoginClick}>Login</button>
                <button className="header-btn primary" onClick={handleRegisterClick}>Join Now</button>
              </>
            )}
          </div>
        </div>
      </header>
      
      <main>
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
          <>
            {/* Live Galaxy View Hero Section */}
            <section className="hero-galaxy-live">
              {/* Scanline effect overlay */}
              <div className="scanline-overlay"></div>

              {/* Left Terminal: Game Features */}
              <div className="terminal-feed">
                <div className="terminal-header">
                  <span className="terminal-title">⚡ GAME FEATURES</span>
                  <span className="terminal-blink">█</span>
                </div>
                <div className="terminal-content">
                  {gameFeatures.map((entry) => (
                    <div key={entry.id} className={`feed-entry feed-${entry.type}`}>
                      <span className="feed-message">{entry.message}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Center: Galaxy Map & Title */}
              <div className="galaxy-center">
                {/* SVG Sector Map */}
                <svg className="sector-map" viewBox="0 0 600 600" xmlns="http://www.w3.org/2000/svg">
                  <defs>
                    <radialGradient id="sectorGlow" cx="50%" cy="50%" r="50%">
                      <stop offset="0%" style={{stopColor: '#4d84fd', stopOpacity: 0.8}} />
                      <stop offset="100%" style={{stopColor: '#4d84fd', stopOpacity: 0}} />
                    </radialGradient>
                    <filter id="glow">
                      <feGaussianBlur stdDeviation="3" result="coloredBlur"/>
                      <feMerge>
                        <feMergeNode in="coloredBlur"/>
                        <feMergeNode in="SourceGraphic"/>
                      </feMerge>
                    </filter>
                  </defs>

                  {/* Warp connections */}
                  <g className="warp-connections" opacity="0.6">
                    <line x1="300" y1="100" x2="450" y2="200" stroke="#00d9ff" strokeWidth="2" className="warp-line" />
                    <line x1="450" y1="200" x2="500" y2="400" stroke="#00d9ff" strokeWidth="2" className="warp-line" />
                    <line x1="500" y1="400" x2="300" y2="500" stroke="#c961de" strokeWidth="2" className="warp-line" />
                    <line x1="300" y1="500" x2="150" y2="400" stroke="#c961de" strokeWidth="2" className="warp-line" />
                    <line x1="150" y1="400" x2="100" y2="200" stroke="#00d9ff" strokeWidth="2" className="warp-line" />
                    <line x1="100" y1="200" x2="300" y2="100" stroke="#00d9ff" strokeWidth="2" className="warp-line" />
                    <line x1="300" y1="300" x2="300" y2="100" stroke="#a855f7" strokeWidth="1.5" className="warp-line" />
                    <line x1="300" y1="300" x2="450" y2="200" stroke="#a855f7" strokeWidth="1.5" className="warp-line" />
                    <line x1="300" y1="300" x2="500" y2="400" stroke="#a855f7" strokeWidth="1.5" className="warp-line" />
                    <line x1="300" y1="300" x2="150" y2="400" stroke="#a855f7" strokeWidth="1.5" className="warp-line" />
                  </g>

                  {/* Sector nodes */}
                  <g className="sector-nodes">
                    <circle cx="300" cy="100" r="8" fill="#00d9ff" filter="url(#glow)" className="sector-node pulse-1" />
                    <circle cx="450" cy="200" r="8" fill="#00d9ff" filter="url(#glow)" className="sector-node pulse-2" />
                    <circle cx="500" cy="400" r="8" fill="#c961de" filter="url(#glow)" className="sector-node pulse-3" />
                    <circle cx="300" cy="500" r="8" fill="#c961de" filter="url(#glow)" className="sector-node pulse-1" />
                    <circle cx="150" cy="400" r="8" fill="#00d9ff" filter="url(#glow)" className="sector-node pulse-2" />
                    <circle cx="100" cy="200" r="8" fill="#00d9ff" filter="url(#glow)" className="sector-node pulse-3" />
                    <circle cx="300" cy="300" r="12" fill="#ffb000" filter="url(#glow)" className="sector-node sector-hub" />
                  </g>

                  {/* Animated ship */}
                  <circle cx="300" cy="100" r="3" fill="#fff" className="ship-marker">
                    <animateMotion dur="8s" repeatCount="indefinite">
                      <mpath href="#shipPath" />
                    </animateMotion>
                  </circle>
                  <path id="shipPath" d="M 300,100 L 450,200 L 500,400 L 300,500 L 150,400 L 100,200 Z" fill="none" />
                </svg>

                {/* Title Overlay */}
                <div className="hero-title-overlay">
                  <h1 className="hero-title-live">
                    <span className="title-line-1">COMMAND THE </span>
                    <span className="title-line-2">GALAXY</span>
                  </h1>
                  <p className="hero-subtitle-live">Neural Link Initialized • AI Consciousness Active</p>
                  <button className="cta-neural-link" onClick={handleRegisterClick}>
                    <span className="cta-icon">⚡</span>
                    <span className="cta-text">INITIALIZE NEURAL LINK</span>
                    <span className="cta-icon">⚡</span>
                  </button>
                </div>
              </div>

              {/* Right Terminal: Galaxy Stats */}
              <div className="terminal-status">
                <div className="terminal-header">
                  <span className="terminal-title">📊 GALAXY STATS</span>
                  <span className="terminal-blink">█</span>
                </div>
                <div className="terminal-content">
                  <div className="status-line">
                    <span className="status-label">SECTORS:</span>
                    <span className="status-value status-cyan">5,300+</span>
                  </div>
                  <div className="status-line">
                    <span className="status-label">SHIP TYPES:</span>
                    <span className="status-value status-purple">9</span>
                  </div>
                  <div className="status-line">
                    <span className="status-label">MILITARY RANKS:</span>
                    <span className="status-value status-green">18</span>
                  </div>
                  <div className="status-line">
                    <span className="status-label">PORT CLASSES:</span>
                    <span className="status-value status-amber">12</span>
                  </div>
                  <div className="status-line status-divider">
                    <span className="status-label">UNIVERSE STATUS:</span>
                  </div>
                  <div className="status-line">
                    <span className="status-value status-success">● ONLINE</span>
                  </div>
                  <div className="status-line">
                    <span className="status-value status-success">● ARIA AI ACTIVE</span>
                  </div>
                  <div className="status-line">
                    <span className="status-value status-success">● WARP NETWORK STABLE</span>
                  </div>
                </div>
              </div>
            </section>

            {/* Revolutionary Features Section */}
            <section className="features-showcase">
              <div className="section-header">
                <h2 className="section-title">Revolutionary Features</h2>
                <p className="section-subtitle">Experience space trading like never before with cutting-edge AI and universe expansion mechanics</p>
              </div>
              
              <div className="features-grid">
                <div className="feature-card featured">
                  <div className="feature-icon-large">🤖</div>
                  <h3>ARIA AI Trading Assistant</h3>
                  <p>World's first learning AI companion that adapts to your trading style, predicts market trends, and optimizes routes in real-time.</p>
                  <div className="feature-tags">
                    <span className="tag">Machine Learning</span>
                    <span className="tag">Personalized</span>
                  </div>
                </div>
                
                <div className="feature-card">
                  <div className="feature-icon-large">🌀</div>
                  <h3>Quantum Warp Tunnels</h3>
                  <p>Build warp gates to reach hidden regions and expand the universe. Create new trade routes to sectors no one has ever seen.</p>
                  <div className="feature-tags">
                    <span className="tag">Universe Expansion</span>
                  </div>
                </div>
                
                <div className="feature-card">
                  <div className="feature-icon-large">🌍</div>
                  <h3>Genesis Devices</h3>
                  <p>Create entirely new planets using rare quantum technology. Transform empty space into thriving worlds.</p>
                  <div className="feature-tags">
                    <span className="tag">Planet Creation</span>
                  </div>
                </div>
                
                <div className="feature-card">
                  <div className="feature-icon-large">⚔️</div>
                  <h3>Strategic Combat</h3>
                  <p>Deploy drones, assault planets, and command fleets with tactical precision. Indestructible escape pods ensure you never lose everything.</p>
                  <div className="feature-tags">
                    <span className="tag">Tactical</span>
                  </div>
                </div>
                
                <div className="feature-card">
                  <div className="feature-icon-large">👥</div>
                  <h3>Real-time Multiplayer</h3>
                  <p>See other players moving through the galaxy instantly. Form teams, collaborate on Genesis projects, and build trading empires together.</p>
                  <div className="feature-tags">
                    <span className="tag">Live Updates</span>
                  </div>
                </div>
                
                <div className="feature-card">
                  <div className="feature-icon-large">📱</div>
                  <h3>Mobile Optimized</h3>
                  <p>Full gameplay experience on any device. Trade on your phone, manage your empire on your tablet, all with seamless synchronization.</p>
                  <div className="feature-tags">
                    <span className="tag">Cross-Platform</span>
                  </div>
                </div>
              </div>
            </section>

            {/* Game Preview Section */}
            <section className="game-preview">
              <div className="section-header">
                <h2 className="section-title">See the Galaxy in Action</h2>
                <p className="section-subtitle">Get a glimpse of the immersive universe that awaits you</p>
              </div>
              
              <div className="preview-showcase">
                <div className="preview-card">
                  <div className="preview-image trading">
                    <div className="mock-ui">
                      <div className="ui-header">🚀 Trading Console</div>
                      <div className="ui-content">
                        <div className="market-item">
                          <span>🔋 Energy Cells</span>
                          <span className="price profit">+32% ↗</span>
                        </div>
                        <div className="market-item">
                          <span>⚙️ Equipment</span>
                          <span className="price loss">-18% ↘</span>
                        </div>
                        <div className="ai-recommendation">
                          <span>🤖 ARIA: Profitable route to Sector 47 detected</span>
                        </div>
                      </div>
                    </div>
                  </div>
                  <h3>AI-Powered Trading</h3>
                  <p>Real-time market analysis with personalized AI recommendations</p>
                </div>
                
                <div className="preview-card">
                  <div className="preview-image exploration">
                    <div className="mock-ui">
                      <div className="ui-header">🌌 Navigation</div>
                      <div className="galaxy-mini">
                        <div className="sector current">You</div>
                        <div className="sector discovered">47</div>
                        <div className="sector unknown">??</div>
                        <div className="warp-tunnel">~~~</div>
                      </div>
                      <div className="ui-action">Build Quantum Tunnel →</div>
                    </div>
                  </div>
                  <h3>Universe Expansion</h3>
                  <p>Discover new sectors and build connections to expand the galaxy</p>
                </div>
                
                <div className="preview-card">
                  <div className="preview-image colonization">
                    <div className="mock-ui">
                      <div className="ui-header">🌍 Colonization</div>
                      <div className="planet-progress">
                        <div className="progress-bar">
                          <div className="progress-fill" style={{width: '73%'}}></div>
                        </div>
                        <div className="progress-text">Genesis Device: 73% Complete</div>
                      </div>
                      <div className="ui-action">New Planet: "New Earth" Ready!</div>
                    </div>
                  </div>
                  <h3>Planet Creation</h3>
                  <p>Use Genesis Devices to create new worlds and expand civilization</p>
                </div>
              </div>
            </section>

            {/* Getting Started Section */}
            <section className="getting-started">
              <div className="section-header">
                <h2 className="section-title">Ready to Command the Galaxy?</h2>
                <p className="section-subtitle">Join thousands of players in the ultimate space trading experience</p>
              </div>
              
              <div className="start-steps">
                <div className="step">
                  <div className="step-number">1</div>
                  <div className="step-content">
                    <h3>Create Your Account</h3>
                    <p>Quick registration gets you into the game in under 30 seconds</p>
                  </div>
                </div>
                
                <div className="step">
                  <div className="step-number">2</div>
                  <div className="step-content">
                    <h3>Meet Your AI Assistant</h3>
                    <p>ARIA will guide you through your first trades and help you understand the market</p>
                  </div>
                </div>
                
                <div className="step">
                  <div className="step-number">3</div>
                  <div className="step-content">
                    <h3>Start Trading & Exploring</h3>
                    <p>Use your 1,000 daily turns to build your empire and discover new sectors</p>
                  </div>
                </div>
              </div>
              
              <div className="final-cta">
                <h3>What are you waiting for?</h3>
                <p>The galaxy needs commanders. Will you answer the call?</p>
                <div className="cta-buttons">
                  <button
                    className="cta-primary large"
                    onClick={handleRegisterClick}
                  >
                    🚀 Start Your Journey
                  </button>
                  <button
                    className="cta-secondary large"
                    onClick={handleLoginClick}
                  >
                    ↩️ Returning Commander
                  </button>
                </div>
              </div>
            </section>

            {/* Latest Updates Section */}
            <section className="updates-section">
              <div className="section-header">
                <h2 className="section-title">Latest Updates</h2>
                <p className="section-subtitle">Stay informed about the newest features and improvements</p>
              </div>

              <div className="updates-grid">
                <div className="update-card">
                  <div className="update-icon">🎖️</div>
                  <div className="update-content">
                    <span className="update-date">March 2026</span>
                    <h3>18-Rank Military Progression System</h3>
                    <p>Full ranking overhaul with 18 military ranks, medals, combat bonuses, and rank-gated ship access.</p>
                  </div>
                </div>

                <div className="update-card">
                  <div className="update-icon">⚔️</div>
                  <div className="update-content">
                    <span className="update-date">February 2026</span>
                    <h3>Fleet Combat with Formation Bonuses</h3>
                    <p>Strategic fleet combat system with formation tactics, drone deployment, and large-scale battle support.</p>
                  </div>
                </div>

                <div className="update-card">
                  <div className="update-icon">🤝</div>
                  <div className="update-content">
                    <span className="update-date">January 2026</span>
                    <h3>Reputation & Faction System</h3>
                    <p>Dynamic reputation tracking across factions with trade bonuses, faction-specific perks, and diplomacy mechanics.</p>
                  </div>
                </div>
              </div>
            </section>
          </>
        )}
      </main>
      
      <footer>
        <p>Sector Wars 2102 - Player Client v0.1.0</p>
      </footer>
    </div>
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
              <Route path="/game" element={
                <ProtectedRoute>
                  <GameDashboard />
                </ProtectedRoute>
              } />
              <Route path="/game/map" element={
                <ProtectedRoute>
                  <GalaxyMap />
                </ProtectedRoute>
              } />
              <Route path="/game/team" element={
                <ProtectedRoute>
                  <TeamManager />
                </ProtectedRoute>
              } />
              <Route path="/game/combat" element={
                <ProtectedRoute>
                  <CombatInterface />
                </ProtectedRoute>
              } />
              <Route path="/game/planets" element={
                <ProtectedRoute>
                  <PlanetManager />
                </ProtectedRoute>
              } />
              <Route path="/game/ships" element={
                <ProtectedRoute>
                  <ShipSelector />
                </ProtectedRoute>
              } />
              <Route path="/game/trading" element={
                <ProtectedRoute>
                  <TradingInterface />
                </ProtectedRoute>
              } />
              <Route path="/game/ranking" element={
                <ProtectedRoute>
                  <RankingPage />
                </ProtectedRoute>
              } />
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
  );
}

export default App