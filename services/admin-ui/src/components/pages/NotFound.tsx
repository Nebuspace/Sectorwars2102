import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import PageHeader from '../ui/PageHeader';

// WO-ADM-FALLBACK-404: an honest 404 instead of the silent redirect-to-
// dashboard that used to mask dead-link regressions. Renders in-shell
// (inside AppLayout, behind the same auth gate every other admin route
// uses) so an admin always knows they hit a route that doesn't exist,
// rather than being silently bounced somewhere else.
const NotFound: React.FC = () => {
  const location = useLocation();

  return (
    <div className="not-found-page">
      <PageHeader
        title="Page Not Found"
        subtitle={`No admin route matches "${location.pathname}".`}
      />
      <div className="card">
        <div className="card-body">
          <p>The page you requested doesn't exist or may have moved.</p>
          <Link to="/dashboard" className="btn btn-primary">
            Return to Dashboard
          </Link>
        </div>
      </div>
    </div>
  );
};

export default NotFound;
