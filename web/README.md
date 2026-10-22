# web — Activity Log UI

Vite + React 18 + TypeScript (strict) single-page app for the activity log and its asynchronous
exports. It implements the browser side of [`docs/CONTRACT.md`](../docs/CONTRACT.md).

- **Activity** (`/activity`): keyset-paginated grid (`GET /api/activity`, "Load more") with area,
  action and UTC date-range filters. **Export** menu (CSV/XLSX × Summary/Detailed) posts the current
  filters to `POST /api/exports` and shows an "Export queued" toast.
- **My exports** (`/exports`, plus a slide-over tray from the header button with an unread badge):
  status, area/mode/format, rows, requested, expires, multi-part progress, and **Download**
  (`POST /api/exports/{id}/download`, then the browser navigates to the presigned URL; 409/410 are
  explained). Opening the tray or the page calls `POST /api/exports/seen`.
- **Background polling**: `GET /api/exports` at 5 s → 10 s → 20 s → 30 s (cap) while any job is
  `queued`/`running`; stops when none are, pauses while the tab is hidden, and restarts at 5 s when
  a new export is queued. A toast fires when a job completes or fails, on any page.

Runtime dependencies are only `react`, `react-dom` and `react-router-dom`. Styling is plain CSS
modules with light/dark themes via `prefers-color-scheme`.

## Scripts

| Script              | What it does                                                                                                                               |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `npm run dev`       | Dev server on :5173; proxies `/api` → `http://localhost:8000` and adds `X-User-Id: demo-user` (dev only; override with `API_PROXY_TARGET`) |
| `npm run dev:mock`  | Same UI with **no backend**: MSW serves `/api` from memory with seeded history; new jobs go queued → running (parts) → completed every 3 s |
| `npm run build`     | Type-check and build to `dist/`                                                                                                            |
| `npm run preview`   | Serve the built `dist/`                                                                                                                    |
| `npm test`          | Vitest + Testing Library (uses the same MSW handlers); `npm test -- --run` for one pass                                                    |
| `npm run lint`      | ESLint (`--max-warnings 0`) + Prettier check                                                                                               |
| `npm run typecheck` | `tsc` for app and tooling configs                                                                                                          |

## Configuration

| Variable           | When          | Default                 | Meaning                                                                  |
| ------------------ | ------------- | ----------------------- | ------------------------------------------------------------------------ |
| `VITE_API_BASE`    | build time    | `""`                    | Prefix for `/api/...` calls. Empty = same origin (nginx proxies `/api`). |
| `API_PROXY_TARGET` | `npm run dev` | `http://localhost:8000` | Where the dev server proxies `/api`.                                     |

The browser never sends `X-User-Id`; the proxy in front of the API does (Vite in dev, nginx otherwise).

## Docker

The [`Dockerfile`](Dockerfile) has three stages:

- `build`: `npm ci` and `npm run build` (build arg `VITE_API_BASE`).
- `dist`: a `scratch` image that contains only the static files, so another nginx image can
  `COPY --from=` it, or you can export it with
  `docker build --target dist --output type=local,dest=./dist-out web`.
- `serve` (default): `nginx:alpine` that serves `dist/` with an SPA fallback (`try_files … /index.html`),
  long-lived caching for hashed `/assets/`, `no-cache` for `index.html`, and an optional `/api/`
  proxy that injects `X-User-Id`. Env: `API_UPSTREAM` (default `http://api:8000`), `DEMO_USER_ID`
  (default `demo-user`), `DNS_RESOLVER` (default Docker's `127.0.0.11`). The upstream is resolved per
  request, so the container starts even if the API is not up yet.

```sh
docker build -t activity-web web                         # standalone UI + /api proxy
docker build --target dist -t activity-web-dist web      # static files only, for the shared nginx
```

If the repo-level nginx serves the UI itself, it needs the same SPA fallback for `/activity` and
`/exports`: `try_files $uri $uri/ /index.html;`.

## Layout

```
src/
  api/          typed fetch client (ApiError), endpoints, contract types
  state/        ExportsProvider (list, polling, transitions → toasts, download, seen) and ToastProvider
  lib/          useBackoffPolling, usePageVisible, formatting, navigation
  components/   AppHeader, ExportsTray (modal dialog), ExportsList, ExportMenu (menu button), StatusPill
  pages/        ActivityPage, ExportsPage
  mocks/        in-memory db + MSW handlers (browser worker for dev:mock, node server for tests)
  test/         Vitest setup and a renderApp helper
mock-public/    MSW service worker, served only in mock mode (never shipped in dist/)
nginx/          nginx template for the standalone image
```

## Accessibility

Built to WCAG 2.1 AA: a skip link; labelled form controls; a visible `:focus-visible` ring; the export
menu follows the WAI-ARIA menu-button pattern (arrow keys, Home/End, Escape returns focus); the tray
is a modal dialog with a focus trap, Escape to close and focus returned to its trigger; toasts render
into persistent `role="status"` (polite) and `role="alert"` (errors) live regions and can be
dismissed; status pills always carry text, not just colour; progress uses a labelled `<progress>`;
motion respects `prefers-reduced-motion`; colour tokens meet 4.5:1 contrast in both themes.
