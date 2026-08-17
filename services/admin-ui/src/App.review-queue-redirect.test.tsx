import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';

/**
 * LEG-103 — mirrors App.tsx `path="review-queue"` Navigate target without
 * mounting the full lazy App shell (auth + providers + code-split pages).
 */
function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="loc">{`${pathname}${search}`}</div>;
}

function renderReviewQueueAlias() {
  return render(
    <MemoryRouter initialEntries={['/review-queue']}>
      <Routes>
        <Route path="review-queue" element={<Navigate to="/audit?tab=review" replace />} />
        <Route path="audit" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('App review-queue route alias (LEG-103)', () => {
  it('redirects /review-queue to /audit?tab=review', () => {
    renderReviewQueueAlias();
    expect(screen.getByTestId('loc')).toHaveTextContent('/audit?tab=review');
  });
});
