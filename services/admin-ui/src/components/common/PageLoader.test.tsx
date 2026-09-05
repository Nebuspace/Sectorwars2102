import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import PageLoader from './PageLoader';

describe('PageLoader lazy-route fallback (LEG-3198)', () => {
  it('renders Loading... text and a spinner container', () => {
    const { container } = render(<PageLoader />);

    expect(screen.getByText('Loading...')).toBeTruthy();

    const spinner = container.querySelector('div[style*="border-radius: 50%"]');
    expect(spinner).toBeTruthy();
    expect(spinner?.getAttribute('style')).toMatch(/40px/);
  });
});
