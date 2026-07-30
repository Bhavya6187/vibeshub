# vibeshub frontend

React 19 + Vite SPA. Serves the trace viewer (public and private, with
private gated on GitHub repo-read access), the marketing/landing page,
user + repo overview pages, GitHub sign-in, and the manual web upload form.

Requires **Node.js 22** (matches CI in [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml)). Node **20.19+** also works with the current Vite 8 toolchain; older 20.x may fail to install.

## Routes

| Path | Page |
|---|---|
| `/` | Landing (signed-out) / link to `/home` (signed-in) |
| `/home` | Redirects signed-in users to their profile |
| `/vibeviewer` | Public no-login transcript upload; success card with copy-link + claim-to-profile |
| `/upload` | Redirects to `/vibeviewer` (retired) |
| `/privacy` | Privacy policy |
| `/contact` | Contact |
| `/faq` | FAQ |
| `/:owner` | User/org profile (traces + repo breakdown + GitHub stats) |
| `/:owner/:repo` | Repo overview (traces + pull requests + contributors) |
| `/:owner/:repo/pull/:number` | All traces attached to a PR |
| `/:owner/:repo/pull/:number/:shortId` | Trace viewer (PR context) |
| `/t/:shortId` | Trace viewer (standalone link) |
| `*` | 404 (`NotFound`) |

The landing page also links to `/blog`, which is **not** a React route — it is
served by the edge in front of vibeshub.ai and 404s under the local dev server.

## Source layout

```
src/
├── App.tsx              # Route table (react-router)
├── main.tsx             # Entry point
├── routes/              # One page component per URL (Landing, TraceView, …)
├── components/          # Shared chrome (PageTopbar, AuthWidget, ThemeToggle, …)
│   └── trace/           # Trace viewer only — parser, Hero, DigestPanel, ProvenanceView, …
│       └── tool/        # Per-tool renderers (ToolCard, ToolGroup, DiffView, BashBody, …)
├── auth/                # AuthContext (AuthProvider + useAuth)
├── assets/              # Landing hero screenshots (light + dark)
├── styles/              # Global CSS + design tokens (theme via data-theme)
├── api.ts               # Fetch wrappers for /api/*
├── useTheme.ts          # Light/dark theme controller
├── types.ts             # Shared API/DTO types
└── tests/               # Vitest unit tests (mirrors routes/ + components/)
```

`App.tsx` wires routes → `src/routes/*`. The viewer itself lives under
`src/components/trace/` (`TraceViewer.tsx` and friends). Above the body sits a
sticky two-tab bar ([`ThreadControls.tsx`](src/components/trace/ThreadControls.tsx)):
**Conversation** (the default) and **Changes** (shown only when the session
touched files). `#changes` in the URL deep-links into the Changes tab; every
other hash lands on Conversation. Conversation renders the prompt rail,
AI-digest chapter rail, outcome cards, collapsible tool calls, nested subagent
threads, activity timeline, slash-command chips, and syntax-highlighted
code/diffs. Changes renders `ProvenanceView`: per-file net diffs with
jump-back links into the conversation.

## Theme and persisted state

- **Theme** — [`useTheme.ts`](src/useTheme.ts) sets `<html data-theme="dark|light">`
  (tokens in `styles/` key off that attribute), updates `meta[name=theme-color]`,
  and stores the choice in `localStorage` under `vibeshub.theme`. First visit
  follows `prefers-color-scheme` (product default is dark). An inline script in
  `index.html` applies the same logic before first paint to avoid a flash.
  [`ThemeToggle`](src/components/ThemeToggle.tsx) calls `toggleTheme()`.
- **Viewer prefs** — [`persistedState.ts`](src/components/trace/persistedState.ts)
  exports `usePersistedBoolean`, a `useState` that mirrors a boolean into
  `localStorage`. Today the viewer uses it for expand-tool-calls
  (`vibeshub.trace.expandToolCalls`). Theme is *not* routed through this helper.

## Local dev

From this directory:

```bash
npm install
npm run dev          # http://127.0.0.1:5173; proxies /api → backend:8000
```

The dev server proxies `/api/*` to `http://127.0.0.1:8000`, so a backend running
at `webapp/backend` is required for authenticated / data-backed pages.

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | `tsc -b` typecheck, then `vite build` |
| `npm run preview` | Serve the production build locally |
| `npm run build:deploy` | Build, then copy `dist/` → `webapp/backend/frontend_dist/` |
| `npm run test` | Vitest unit tests (one shot) |
| `npm run test:watch` | Vitest watch mode |
| `npm run test:e2e` | Playwright e2e (see below) |

There is no separate ESLint / Prettier script today; typechecking is the
`tsc -b` step inside `npm run build`.

## Tests

```bash
npm run test         # vitest unit tests
npm run test:e2e     # playwright
```

### Playwright prerequisites

`test:e2e` uses [`e2e/playwright.config.ts`](e2e/playwright.config.ts). Before
the first run (and after Playwright upgrades):

```bash
npx playwright install    # downloads browser binaries
```

The config starts its own Vite dev server (`npm run dev -- --host 127.0.0.1`)
and does **not** start the backend — specs stub `/api/*` with `page.route`.
No separate backend process is required for e2e. `reuseExistingServer` is
`false`, so an already-running `:5173` will conflict; stop local `npm run dev`
first, or free the port.

## Deploy build

```bash
npm run build:deploy
```

This runs the full build (`tsc -b` typecheck, then `vite build`), then clears
`webapp/backend/frontend_dist/` and copies `dist/` into it.
The Azure deploy Dockerfile at [`deploy/azure/Dockerfile`](../../deploy/azure/Dockerfile)
picks up the `frontend_dist/` directory, and FastAPI serves it as the SPA.
