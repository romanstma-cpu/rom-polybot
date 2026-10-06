# Contributing to ROM PolyBot

Thanks for your interest! This is a real-money trading app, so correctness and safety matter — please
keep changes small, tested, and easy to review.

## Development setup

**Prerequisites:** Node.js 22 and Python 3.12+ on PATH (what CI uses).

```bash
npm install
npm run dev          # vite + electron + python backend; `predev` sets up python/.venv
```

## Before you open a PR

```bash
npm run typecheck    # TypeScript must pass (renderer + electron)
npm run py:test      # Python tests must pass
npm run test:e2e:all # Electron end-to-end suites (Windows; run after npm run build)
```

The first two are enforced by CI on every pull request. The Windows installer job
runs every e2e suite, so a UI change that breaks one blocks the release.

E2E suites must wait on real state. `page.waitForFunction` with an async predicate
returns immediately (a Promise is truthy), so use `waitUntil` / `backendRunning` from
`e2e/wait.mjs` instead.

## Guidelines

- **Match the surrounding code.** Naming, comment density, and idioms should look native to the file.
- **Add tests for logic changes** — especially anything touching the trading engine, sizing, P&L, or
  reconciliation. The Python tests live in `python/tests/`.
- **Never commit secrets or local state.** No private keys, API credentials, `.env` files, local databases, or
  logs. The `.gitignore` is set up to prevent this — don't override it.
- **Be careful with money paths.** Changes to order placement, settlement, or reconciliation should be
  conservative and well-tested. When in doubt, gate new behavior behind a config flag that defaults off.
- **Keep PRs focused.** One logical change per PR, with a clear description of what and why.

## Architecture quick reference

- `electron/` — main process, preload (`window.rom` API surface), IPC, system integration.
- `python/` — backend: `service.py` (RPC loop), `scanner.py`, `trader.py`, `crypto15m*.py`, `db.py`.
  Renderer ↔ backend talk JSON-RPC over stdio.
- `src/` — React UI. `shared/types.ts` is the IPC contract shared by both sides.

## Reporting bugs / security issues

Open a GitHub issue for bugs. For **security** issues, follow [SECURITY.md](./SECURITY.md) instead of
filing a public issue.
