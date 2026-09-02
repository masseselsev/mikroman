import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '../test/render';
import { Navbar } from './Navbar';

// RouterCommentBar loads its own data; stub the client so mounting is cheap.
vi.mock('../api/client', () => ({
  api: { updateRouter: vi.fn().mockResolvedValue({}) },
}));

const base = {
  isConnected: true,
  routerInfo: null,
  routers: [{ id: 1, name: 'Main', is_default: true, comment: 'patch panel B' }],
  onSelectRouter: vi.fn(),
  onOpenSettings: vi.fn(),
  onAddRouter: vi.fn(),
  onRouterCommentSaved: vi.fn(),
};

describe('Navbar router comment bar', () => {
  it('shows the selected router note when activeRouter is passed', () => {
    // Regression: the multi-router refactor dropped activeRouter from the
    // Navbar props, so the note strip stopped rendering entirely.
    renderWithProviders(<Navbar {...base} activeRouter={base.routers[0]} />);
    expect(screen.getByText('patch panel B')).toBeInTheDocument();
  });

  it('renders nothing for the note when no router is active', () => {
    renderWithProviders(<Navbar {...base} activeRouter={null} />);
    expect(screen.queryByText('patch panel B')).toBeNull();
  });
});
