import React from 'react';
import { render } from '@testing-library/react';
import { I18nProvider } from '../context/I18nContext';
import { ThemeProvider } from '../context/ThemeContext';

/**
 * Render a component inside the providers it expects.
 *
 * Nearly every component calls `useI18n`, and several call `useTheme`; without
 * the providers they fall back to the context defaults, where `t` returns the
 * key. Tests would then assert on key names instead of the text a user reads,
 * which is exactly the kind of test that passes while the UI is broken.
 */
export function renderWithProviders(ui, options = {}) {
  const Wrapper = ({ children }) => (
    <ThemeProvider>
      <I18nProvider>{children}</I18nProvider>
    </ThemeProvider>
  );
  return render(ui, { wrapper: Wrapper, ...options });
}

export * from '@testing-library/react';
