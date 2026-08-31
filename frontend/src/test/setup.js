// Shared setup for every frontend test.
//
// `jest-dom` adds the DOM-aware assertions the component tests rely on
// (toBeInTheDocument, toHaveAttribute, and so on).
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Testing Library does not unmount between tests on its own when globals are
// enabled through Vitest rather than Jest, and a leaked component keeps its
// timers and listeners running into the next test.
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
