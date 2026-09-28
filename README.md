# ROM Polybot

Independent desktop trading app configured for the Polymarket US retail API.

## Windows app
Run `release/ROM PolyBot-Setup-2.36.2.exe`, then open ROM PolyBot.
Python and the app runtime are included in the installer.

## In-app updates
ROM PolyBot checks for stable releases after launch and periodically while open.
It downloads a matching update in the background and verifies the installer
against the SHA-256 checksum published with that release. Settings shows the
installed version, progress, errors, and a retry option. Installing an update
requires an explicit action so running strategies are not interrupted without
warning. On Windows the verified installer opens from the app. On Apple Silicon,
the app opens the verified DMG so you can replace the app in Applications.
Versions before 2.36.2 need one final manual installer download to gain this
feature.

## Development
Install Node.js and Python 3.12+, run `npm install`, then `npm run dev`.
For a Windows installer, run `npm run dist`.

Open API and enter the Key ID and Secret Key from https://polymarket.us/developer.
No wallet, referral signup, or additional API subscription is required. Account
verification and funding are handled in Polymarket US.

Settings and encrypted credentials use ROM PolyBot's separate application-data directory.
The original app and its saved data are not migrated.

US market availability differs. Large-trade and momentum scanners consume the
authenticated US trade stream. Those features must not silently use
international exchange data. Crypto strategies need US-listed markets.

Original third-party copyright notices are retained in LICENSE as required.

## Releasing
1. Set the new version with `npm version <version> --no-git-tag-version`, add
   `docs/release-notes/release-notes-<version>.md`, and merge both to master.
2. On GitHub, open Actions → Build Installers → Run workflow, keep `master`,
   and type the version into "Release version". Once both installers are built
   and tested, the run tags that commit `v<version>` and publishes the release.
3. Installs on 2.35.11 and earlier look for updates in rom-apps, so run
   "Mirror a PolyBot release for older installs" there with the same version.

## Verification
The full 2,003-test Python suite completes. The 23 Electron script-arming checks pass, and
the packaged Windows app has been opened and its API screen inspected.
Public US market data was verified. Authenticated account access, streaming,
and live trading still require validation with the user's own US credentials.

International wallet-only tests are retained as text in
`python/tests/international_reference`; US API contract tests replace them.
