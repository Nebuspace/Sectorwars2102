import React, { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import { AdminProvider } from './contexts/AdminContext';
import { WebSocketProvider } from './contexts/WebSocketContext';
import { ToastProvider } from './contexts/ToastContext';
import './App.css';

// Layouts
import AppLayout from './components/layouts/AppLayout';

// Components
import ProtectedRoute from './components/auth/ProtectedRoute';
import PageLoader from './components/common/PageLoader';
import ErrorBoundary from './components/common/ErrorBoundary';

// Lazy load all pages for better performance
const LoginPage = lazy(() => import('./components/pages/LoginPage'));
const Dashboard = lazy(() => import('./components/pages/Dashboard'));
const UsersManager = lazy(() => import('./components/pages/UsersManager'));
const UniverseManager = lazy(() => import('./components/pages/UniverseManager'));
const EconomyDashboard = lazy(() => import('./components/pages/EconomyDashboard'));
const PlayerAnalytics = lazy(() => import('./components/pages/PlayerAnalytics'));
const CombatOverview = lazy(() => import('./components/pages/CombatOverview').then(module => ({
  default: module.CombatOverview
})));
const ContractDisputeArbitration = lazy(() => import('./components/pages/ContractDisputeArbitration').then(module => ({
  default: module.ContractDisputeArbitration
})));
const FleetManagement = lazy(() => import('./components/pages/FleetManagement'));
const TeamManagement = lazy(() => import('./components/pages/TeamManagement'));
const EventManagement = lazy(() => import('./components/pages/EventManagement'));
const SectorsManager = lazy(() => import('./components/pages/SectorsManager'));
const PlanetsManager = lazy(() => import('./components/pages/PlanetsManager'));
const StationsManager = lazy(() => import('./components/pages/StationsManager'));
const WarpTunnelsManager = lazy(() => import('./components/pages/WarpTunnelsManager'));
const SecurityDashboard = lazy(() => import('./components/pages/SecurityDashboard').then(module => ({
  default: module.SecurityDashboard
})));
const PermissionsDashboard = lazy(() => import('./components/pages/PermissionsDashboard').then(module => ({
  default: module.PermissionsDashboard
})));
const AdvancedAnalytics = lazy(() => import('./components/pages/AdvancedAnalytics').then(module => ({
  default: module.AdvancedAnalytics
})));
const ColonizationManagement = lazy(() => import('./components/pages/ColonizationManagement').then(module => ({
  default: module.ColonizationManagement
})));
const AITradingDashboard = lazy(() => import('./components/pages/AITradingDashboard'));
const CentralNexusManager = lazy(() => import('./components/pages/CentralNexusManager'));
const RegionalGovernorDashboard = lazy(() => import('./components/pages/RegionalGovernorDashboard'));
const FirstLoginConversations = lazy(() => import('./components/pages/FirstLoginConversations'));
const BangGalaxyPage = lazy(() => import('./components/pages/BangGalaxyPage'));
const FactionManagement = lazy(() => import('./components/pages/FactionManagement'));
const MessageModeration = lazy(() => import('./components/pages/MessageModeration'));
const TranslationManagement = lazy(() => import('./components/pages/TranslationManagement'));
const NotFound = lazy(() => import('./components/pages/NotFound'));

// Helper component for protected lazy routes.
// The ErrorBoundary is keyed by pathname so a crash on one page resets
// when the admin navigates elsewhere.
const ProtectedLazyRoute: React.FC<{ element: React.ReactElement }> = ({ element }) => {
  const location = useLocation();
  return (
    <ProtectedRoute>
      <ErrorBoundary key={location.pathname}>
        <Suspense fallback={<PageLoader />}>
          {element}
        </Suspense>
      </ErrorBoundary>
    </ProtectedRoute>
  );
};

function App() {
  return (
    <AuthProvider>
      <AdminProvider>
        <WebSocketProvider>
          <ToastProvider>
          <Router basename={import.meta.env.BASE_URL.replace(/\/$/, '') || '/'}>
            <Routes>
              <Route path="/" element={<AppLayout />}>
                {/* Public routes */}
                <Route path="login" element={
                  <ErrorBoundary>
                    <Suspense fallback={<PageLoader />}>
                      <LoginPage />
                    </Suspense>
                  </ErrorBoundary>
                } />

                {/* Protected routes */}
                <Route path="dashboard" element={<ProtectedLazyRoute element={<Dashboard />} />} />
                <Route path="users" element={<ProtectedLazyRoute element={<UsersManager />} />} />
                <Route path="universe" element={<ProtectedLazyRoute element={<UniverseManager />} />} />
                <Route path="economy" element={<ProtectedLazyRoute element={<EconomyDashboard />} />} />
                <Route path="players" element={<ProtectedLazyRoute element={<PlayerAnalytics />} />} />
                <Route path="combat" element={<ProtectedLazyRoute element={<CombatOverview />} />} />
                <Route path="contract-disputes" element={<ProtectedLazyRoute element={<ContractDisputeArbitration />} />} />
                <Route path="fleets" element={<ProtectedLazyRoute element={<FleetManagement />} />} />
                <Route path="colonies" element={<ProtectedLazyRoute element={<ColonizationManagement />} />} />
                <Route path="teams" element={<ProtectedLazyRoute element={<TeamManagement />} />} />
                <Route path="events" element={<ProtectedLazyRoute element={<EventManagement />} />} />
                <Route path="analytics" element={<ProtectedLazyRoute element={<AdvancedAnalytics />} />} />
                <Route path="security" element={<ProtectedLazyRoute element={<SecurityDashboard />} />} />
                <Route path="permissions" element={<ProtectedLazyRoute element={<PermissionsDashboard />} />} />
                <Route path="ai-trading" element={<ProtectedLazyRoute element={<AITradingDashboard />} />} />
                <Route path="sectors" element={<ProtectedLazyRoute element={<SectorsManager />} />} />

                {/* Universe CRUD Routes */}
                <Route path="universe/bang" element={<ProtectedLazyRoute element={<BangGalaxyPage />} />} />
                <Route path="universe/sectors" element={<ProtectedLazyRoute element={<SectorsManager />} />} />
                <Route path="universe/planets" element={<ProtectedLazyRoute element={<PlanetsManager />} />} />
                <Route path="universe/stations" element={<ProtectedLazyRoute element={<StationsManager />} />} />
                <Route path="universe/warptunnels" element={<ProtectedLazyRoute element={<WarpTunnelsManager />} />} />
                <Route path="nexus" element={<ProtectedLazyRoute element={<CentralNexusManager />} />} />

                {/* Regional Governance Routes */}
                <Route path="regional-governor" element={<ProtectedLazyRoute element={<RegionalGovernorDashboard />} />} />

                {/* First Login Conversations */}
                <Route path="first-login-conversations" element={<ProtectedLazyRoute element={<FirstLoginConversations />} />} />

                {/* Surfaced admin subsystems (run 5) */}
                <Route path="factions" element={<ProtectedLazyRoute element={<FactionManagement />} />} />
                <Route path="messages" element={<ProtectedLazyRoute element={<MessageModeration />} />} />
                <Route path="translations" element={<ProtectedLazyRoute element={<TranslationManagement />} />} />

                {/* Redirect root to dashboard */}
                <Route path="/" element={<Navigate to="/dashboard" replace />} />

                {/* Fallback route - an honest 404 instead of a silent redirect
                    (WO-ADM-FALLBACK-404). Uses the same ProtectedLazyRoute
                    wrapper every other route does: AppLayout above already
                    redirects a logged-out admin to /login with state.from
                    preserved before this route's element ever renders, and
                    ProtectedRoute is the second, defense-in-depth guard on
                    this specific route. */}
                <Route path="*" element={<ProtectedLazyRoute element={<NotFound />} />} />
              </Route>
            </Routes>
          </Router>
          </ToastProvider>
        </WebSocketProvider>
      </AdminProvider>
    </AuthProvider>
  );
}

export default App;