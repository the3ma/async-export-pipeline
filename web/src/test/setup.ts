import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest';
import { db } from '../mocks/db';
import { server } from '../mocks/server';

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
beforeEach(() => db.reset());
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
