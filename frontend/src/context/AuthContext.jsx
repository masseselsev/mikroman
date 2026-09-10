import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api, setUnauthorizedHandler } from '../api/client';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [authEnabled, setAuthEnabled] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [username, setUsername] = useState('admin');
  const [isLoading, setIsLoading] = useState(true);

  const checkStatus = useCallback(async () => {
    try {
      const res = await api.getAuthStatus();
      const data = res.data || {};
      setAuthEnabled(Boolean(data.auth_enabled));
      setIsAuthenticated(Boolean(data.authenticated));
      setNeedsSetup(Boolean(data.needs_setup));
      if (data.username) {
        setUsername(data.username);
      }
    } catch (err) {
      console.warn('Could not determine auth status:', err.message);
      // Fail closed: if auth check errors, assume auth is required
      setAuthEnabled(true);
      setIsAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    checkStatus();

    // Register 401 interceptor callback
    setUnauthorizedHandler(() => {
      setIsAuthenticated(false);
    });

    return () => {
      setUnauthorizedHandler(null);
    };
  }, [checkStatus]);

  const login = async (password) => {
    const res = await api.login(password);
    setIsAuthenticated(true);
    setNeedsSetup(false);
    if (res.data?.username) {
      setUsername(res.data.username);
    }
    return res;
  };

  const setup = async (password) => {
    const res = await api.setupAdmin(password);
    setIsAuthenticated(true);
    setNeedsSetup(false);
    if (res.data?.username) {
      setUsername(res.data.username);
    }
    return res;
  };

  const logout = async () => {
    try {
      await api.logout();
    } catch (err) {
      console.warn('Logout request failed:', err.message);
    } finally {
      setIsAuthenticated(false);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        authEnabled,
        isAuthenticated,
        needsSetup,
        username,
        isLoading,
        login,
        setup,
        logout,
        checkStatus,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    return {
      authEnabled: false,
      isAuthenticated: false,
      needsSetup: false,
      username: 'admin',
      isLoading: false,
      login: async () => {},
      setup: async () => {},
      logout: async () => {},
      checkStatus: async () => {},
    };
  }
  return context;
}
