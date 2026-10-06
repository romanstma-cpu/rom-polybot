# ROM Polybot

A desktop workspace for trading on [Polymarket US](https://polymarket.us) through its
retail API. Scan the market, practice with simulated funds, set your own limits, and
see why every candidate was skipped, practiced or sent before you decide to go live.

**Download:** [romapps.xyz](https://romapps.xyz/#download-polybot) · Windows 10/11 (x64)
and macOS 15+ on Apple Silicon · [Release notes](https://romapps.xyz/changelog.html)

> Trading starts paused. Live orders stay off until your account is connected, risk
> limits are saved, evidence qualifies and buying power is available. No strategy has
> demonstrated profitable live trading, and automated trading can lose money. Read the
> [disclaimer](DISCLAIMER.md) before using real funds.

## What it does

| | |
|---|---|
| **Scan** | Watches Polymarket US markets, large trades and momentum from the authenticated US trade stream. US features never silently use international exchange data. |
| **Practice** | Runs the main strategy on a simulated $1,000 balance kept apart from your account. Fills are estimates: entry price plus the applicable US taker fee. |
| **Limit** | Maximum per position, portfolio exposure, cash reserve and a daily loss stop. While live, an emergency stop in the title bar pauses the strategy and cancels its pending orders. |
| **Explain** | Decision history, the opportunity funnel and the Evidence screen show what was skipped and why, and what live trading still needs. |
| **Evidence** | Live Large Trade and Momentum entries need a qualified score group, tested on later outcomes, and a positive margin after fees. Shadow models report only; they cannot change an order. |
| **Update** | Checks for stable releases, downloads in the background and verifies the installer's SHA-256 before offering it. Installing always waits for you. |

Also included: a 15-minute crypto engine, a sandboxed Python script engine, replay and
backtests, a live terminal and visualizer, profiles, and CSV export of trade history.

## Getting started

1. Install from [romapps.xyz](https://romapps.xyz/#download-polybot). Python and the app
   runtime are bundled. Installers are unsigned and the Mac build is not notarized;
   [verify the checksum](https://romapps.xyz/code-signing-policy.html) first.
2. Open **API** and enter the Key ID and Secret Key from
   [polymarket.us/developer](https://polymarket.us/developer). No wallet, referral
   signup or additional subscription is needed; account verification and funding happen
   in Polymarket US.
3. Review your limits on **Strategy**, then start **Practice**.

Credentials are encrypted locally (DPAPI on Windows, the OS keychain elsewhere) and are
never returned to the renderer. See [SECURITY.md](SECURITY.md). The app sends no
telemetry.

## In-app updates

ROM PolyBot checks for stable releases after launch and periodically while open. It
downloads a matching update in the background and verifies the installer against the
SHA-256 checksum published with that release. Settings shows the installed version,
progress, errors, and a retry option. Installing an update requires an explicit action
so running strategies are not interrupted without warning. On Windows the verified
installer opens from the app. On Apple Silicon, the app opens the verified DMG so you can
replace the app in Applications. Versions before 2.36.2 need one final manual installer
download to gain this feature.

## How it is built

```
React renderer (src/)  ──window.rom──▶  Electron main (electron/)  ──JSON-RPC over stdio──▶  Python backend (python/)
   pages, state                          IPC, validation, updates,                           scanner, trader, risk,
   shared/types.ts contract              tray, backend supervisor                            SQLite journal, US API
```

- `electron/system/python-backend.ts` supervises the backend: fast restarts that slow down
  after repeated start-up crashes, a ping watchdog, and pending-request rejection on exit.
- `electron/system/config-validate.ts` rejects malformed config before it reaches Python.
- `python/service.py` is the RPC loop; `trader.py`, `scanner.py`, `account_risk.py`,
  `order_journal.py` and `crypto15m*.py` hold the engines.
- `python/livecheck/` is an operator-run, staged check against a real account: read-only
  stages first, then one cancellable order behind a typed confirmation.

Settings and encrypted credentials use ROM PolyBot's own application-data directory.
Large Trade and Momentum need the authenticated US trade stream; crypto strategies need
US-listed markets. Original third-party copyright notices are retained in
[LICENSE](LICENSE).

## Development

Install Node.js 22 and Python 3.12+, then:

```bash
npm install
npm run dev        # Vite + Electron + backend; predev creates python/.venv
```

| Command | What it does |
|---|---|
| `npm run typecheck` | TypeScript for the renderer and Electron |
| `npm run py:test` | The Python suite (about 2,200 tests) |
| `npm run test:e2e:all` | Every Electron end-to-end suite in `e2e/`, each retried once |
| `npm run check:ui` | Drives the real app, screenshots every page, audits contrast, type scale and hit targets |
| `npm run release:gate` | Everything above that the platform supports, plus the production build |
| `npm run dist` | Build the renderer, bundle the backend and package an installer |

CI runs the release gate on every pull request, on every push to `master`, and weekly so
a date-dependent test fails there before it can fail a release. The Windows installer
job also runs the UI audit and every e2e suite. See [CONTRIBUTING.md](CONTRIBUTING.md)
and [docs/TESTING.md](docs/TESTING.md).

## Releasing

1. Set the new version with `npm version <version> --no-git-tag-version`, add
   `docs/release-notes/release-notes-<version>.md`, and merge both to master.
2. On GitHub, open Actions → Build Installers → Run workflow, keep `master`,
   and type the version into "Release version". Once both installers are built
   and tested, the run tags that commit `v<version>` and publishes the release.
3. Installs on 2.35.11 and earlier look for updates in rom-apps, so run
   "Mirror a PolyBot release for older installs" there with the same version.

## Verification status

The Python suite, the Electron e2e suites and the UI audit pass, and the packaged
Windows app has been opened and its API screen inspected. Public US market data was
verified. Authenticated account access, streaming and live trading still require
validation with the user's own US credentials (`python -m livecheck.live_order
--preflight-only` first).

International wallet-only tests are retained as text in
`python/tests/international_reference`; US API contract tests replace them.
