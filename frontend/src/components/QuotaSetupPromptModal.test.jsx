import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../test/render';
import { QuotaSetupPromptModal } from './QuotaSetupPromptModal';

describe('QuotaSetupPromptModal', () => {
  const router = { id: 42, name: 'WIN-HOST-A', host: '192.0.2.1' };
  let onConfigure;
  let onDismiss;

  beforeEach(() => {
    onConfigure = vi.fn();
    onDismiss = vi.fn();
    localStorage.clear();
  });

  it('renders nothing when closed or without router', () => {
    const { container } = renderWithProviders(
      <QuotaSetupPromptModal isOpen={false} router={router} onConfigure={onConfigure} onDismiss={onDismiss} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders router name and prompt message when open', () => {
    renderWithProviders(
      <QuotaSetupPromptModal isOpen={true} router={router} onConfigure={onConfigure} onDismiss={onDismiss} />
    );
    expect(screen.getByText('WIN-HOST-A')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /configure quota/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /leave unmetered/i })).toBeInTheDocument();
  });

  it('calls onDismiss and records preference in localStorage on dismiss', () => {
    renderWithProviders(
      <QuotaSetupPromptModal isOpen={true} router={router} onConfigure={onConfigure} onDismiss={onDismiss} />
    );
    fireEvent.click(screen.getByRole('button', { name: /leave unmetered/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('mikroman:quota-prompt-dismissed-42')).toBe('true');
  });

  it('calls onConfigure and records preference in localStorage on configure', () => {
    renderWithProviders(
      <QuotaSetupPromptModal isOpen={true} router={router} onConfigure={onConfigure} onDismiss={onDismiss} />
    );
    fireEvent.click(screen.getByRole('button', { name: /configure quota/i }));
    expect(onConfigure).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('mikroman:quota-prompt-dismissed-42')).toBe('true');
  });
});

