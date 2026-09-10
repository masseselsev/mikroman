import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, fireEvent, waitFor } from '../test/render';
import { LoginPage } from './LoginPage';
import * as AuthContextModule from '../context/AuthContext';

describe('LoginPage component', () => {
  it('renders standard login elements when needsSetup is false', () => {
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: false,
      login: vi.fn(),
      setup: vi.fn(),
    });

    renderWithProviders(<LoginPage />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Sign in to MikroMan|Вход в MikroMan/i);
    expect(screen.getByLabelText(/Admin Password|Пароль админа/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Confirm Password|Подтверждение/i)).toBeNull();
    expect(screen.getByRole('button', { name: /Log In|Войти/i })).toBeInTheDocument();
  });

  it('toggles password field between password and text type', () => {
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: false,
      login: vi.fn(),
      setup: vi.fn(),
    });

    renderWithProviders(<LoginPage />);

    const passwordInput = screen.getByLabelText(/Admin Password|Пароль админа/i);
    expect(passwordInput.type).toBe('password');

    const toggleBtn = screen.getByLabelText(/Toggle password visibility/i);
    fireEvent.click(toggleBtn);
    expect(passwordInput.type).toBe('text');

    fireEvent.click(toggleBtn);
    expect(passwordInput.type).toBe('password');
  });

  it('submits password to login handler', async () => {
    const mockLogin = vi.fn().mockResolvedValue({});
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: false,
      login: mockLogin,
      setup: vi.fn(),
    });

    renderWithProviders(<LoginPage />);

    const passwordInput = screen.getByLabelText(/Admin Password|Пароль админа/i);
    fireEvent.change(passwordInput, { target: { value: 'mypassword123' } });

    const submitBtn = screen.getByRole('button', { name: /Log In|Войти/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('mypassword123');
    });
  });

  it('renders setup elements when needsSetup is true', () => {
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: true,
      login: vi.fn(),
      setup: vi.fn(),
    });

    renderWithProviders(<LoginPage />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Create Admin Password|Создание пароля админа/i);
    expect(screen.getByPlaceholderText(/Re-enter password|Повторите пароль/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Set Password & Continue|Установить пароль/i })).toBeInTheDocument();
  });

  it('validates minimum password length in setup mode', async () => {
    const mockSetup = vi.fn();
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: true,
      login: vi.fn(),
      setup: mockSetup,
    });

    renderWithProviders(<LoginPage />);

    const passwordInput = screen.getByLabelText(/Admin Password|Пароль админа/i);
    const confirmInput = screen.getByPlaceholderText(/Re-enter password|Повторите пароль/i);

    fireEvent.change(passwordInput, { target: { value: 'short' } });
    fireEvent.change(confirmInput, { target: { value: 'short' } });

    const submitBtn = screen.getByRole('button', { name: /Set Password & Continue|Установить пароль/i });
    fireEvent.click(submitBtn);

    expect(mockSetup).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/at least 8 characters|не менее 8 символов/i);
  });

  it('validates matching passwords in setup mode', async () => {
    const mockSetup = vi.fn();
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: true,
      login: vi.fn(),
      setup: mockSetup,
    });

    renderWithProviders(<LoginPage />);

    const passwordInput = screen.getByLabelText(/Admin Password|Пароль админа/i);
    const confirmInput = screen.getByPlaceholderText(/Re-enter password|Повторите пароль/i);

    fireEvent.change(passwordInput, { target: { value: 'password123' } });
    fireEvent.change(confirmInput, { target: { value: 'password456' } });

    const submitBtn = screen.getByRole('button', { name: /Set Password & Continue|Установить пароль/i });
    fireEvent.click(submitBtn);

    expect(mockSetup).not.toHaveBeenCalled();
    expect(screen.getByText(/Passwords do not match|Пароли не совпадают/i)).toBeInTheDocument();
  });

  it('submits valid credentials to setup handler', async () => {
    const mockSetup = vi.fn().mockResolvedValue({});
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: true,
      login: vi.fn(),
      setup: mockSetup,
    });

    renderWithProviders(<LoginPage />);

    const passwordInput = screen.getByLabelText(/Admin Password|Пароль админа/i);
    const confirmInput = screen.getByPlaceholderText(/Re-enter password|Повторите пароль/i);

    fireEvent.change(passwordInput, { target: { value: 'strongpassword123' } });
    fireEvent.change(confirmInput, { target: { value: 'strongpassword123' } });

    const submitBtn = screen.getByRole('button', { name: /Set Password & Continue|Установить пароль/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockSetup).toHaveBeenCalledWith('strongpassword123');
    });
  });

  it('renders error alert when login fails', async () => {
    const mockLogin = vi.fn().mockRejectedValue(new Error('Invalid password provided'));
    vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
      needsSetup: false,
      login: mockLogin,
      setup: vi.fn(),
    });

    renderWithProviders(<LoginPage />);

    const passwordInput = screen.getByLabelText(/Admin Password|Пароль админа/i);
    fireEvent.change(passwordInput, { target: { value: 'wrongpassword' } });

    const submitBtn = screen.getByRole('button', { name: /Log In|Войти/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText('Invalid password provided')).toBeInTheDocument();
    });
  });
});
