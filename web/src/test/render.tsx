import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { App } from '../App';

interface Options {
  route?: string;
  pollMinMs?: number;
  pollMaxMs?: number;
}

/** Render the whole app (providers + routes) at `route`, with a fast poll schedule. */
export function renderApp({ route = '/activity', pollMinMs = 20, pollMaxMs = 80 }: Options = {}) {
  const user = userEvent.setup();
  const utils = render(
    <MemoryRouter initialEntries={[route]}>
      <App pollMinMs={pollMinMs} pollMaxMs={pollMaxMs} />
    </MemoryRouter>,
  );
  return { user, ...utils };
}
