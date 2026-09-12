import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CountryFlag } from './CountryFlag';

describe('CountryFlag component', () => {
  it('renders vector flag for known countries', () => {
    const { rerender } = render(<CountryFlag code="IN" />);
    expect(screen.getByLabelText('India')).toBeInTheDocument();

    rerender(<CountryFlag code="ES" />);
    expect(screen.getByLabelText('Spain')).toBeInTheDocument();

    rerender(<CountryFlag code="CH" />);
    expect(screen.getByLabelText('Switzerland')).toBeInTheDocument();

    rerender(<CountryFlag code="CN" />);
    expect(screen.getByLabelText('China')).toBeInTheDocument();

    rerender(<CountryFlag code="AQ" />);
    expect(screen.getByLabelText('Antarctica')).toBeInTheDocument();

    rerender(<CountryFlag code="UN" />);
    expect(screen.getByLabelText('Global Internet')).toBeInTheDocument();
  });

  it('renders stylized ISO badge for unlisted 2-letter country code', () => {
    render(<CountryFlag code="ZZ" />);
    const badge = screen.getByLabelText('ZZ');
    expect(badge).toBeInTheDocument();
    expect(screen.getByText('ZZ')).toBeInTheDocument();
  });

  it('renders local icon for LOCAL code', () => {
    render(<CountryFlag code="LOCAL" />);
    expect(screen.getByLabelText('Local Network')).toBeInTheDocument();
  });
});
