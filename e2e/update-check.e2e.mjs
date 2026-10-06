import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

if (process.platform === 'darwin' && process.arch !== 'arm64') {
  console.log('SKIP: Apple Silicon updater E2E requires an arm64 macOS runner');
  process.exit(0);
}
// No Linux installer is published, so the updater has nothing to offer there.
// CI's Linux runner also has no display to start Electron, which made every
// CI run since these suites joined the gate fail. The Windows and Apple
// Silicon release builds still run them.
if (process.platform === 'linux') {
  console.log('SKIP: in-app updates ship for Windows and Apple Silicon only');
  process.exit(0);
}

const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'rom-updates-'));
const currentVersion = JSON.parse(fs.readFileSync(path.resolve('package.json'), 'utf8')).version;
const [major, minor, patch] = currentVersion.split('.').map(Number);
const nextVersion = `${major}.${minor}.${patch + 1}`;
const installerName = process.platform === 'darwin'
  ? `ROM.PolyBot-${nextVersion}-arm64.dmg`
  : `ROM.PolyBot-Setup-${nextVersion}.exe`;
const checksumName = process.platform === 'darwin'
  ? 'SHA256SUMS-mac-apple-silicon.txt'
  : 'SHA256SUMS-windows-x64.txt';
for (const dir of ['Roaming', 'Local', 'profile']) fs.mkdirSync(path.join(sandbox, dir));
const app = await electron.launch({
  executablePath: path.resolve('node_modules', 'electron', 'dist',
    process.platform === 'win32' ? 'electron.exe'
      : process.platform === 'darwin' ? 'Electron.app/Contents/MacOS/Electron' : 'electron'),
  args: [process.cwd(), `--user-data-dir=${path.join(sandbox, 'profile')}`],
  env: { ...process.env, APPDATA: path.join(sandbox, 'Roaming'), LOCALAPPDATA: path.join(sandbox, 'Local') },
  timeout: 30_000,
});

try {
  const page = await app.firstWindow();
  await app.evaluate((_electron, { newerVersion, installerName, checksumName }) => {
    globalThis.__originalFetch = globalThis.fetch;
    globalThis.fetch = async (url) => {
      if (!String(url).includes('/releases?per_page=100&page=1')) {
        throw new Error(`Unexpected update URL: ${url}`);
      }
      return {
        ok: true,
        status: 200,
        json: async () => [
          { tag_name: 'v99.0.0', html_url: 'https://github.com/romanstma-cpu/rom-polybot/releases/tag/v99.0.0', assets: [{ name: 'ROM.Trader-Setup-99.0.0.exe' }] },
          { tag_name: 'v99.0.1', prerelease: true, assets: [{ name: installerName.replace(newerVersion, '99.0.1') }, { name: checksumName }] },
          { tag_name: 'v99.0.2', assets: [{ name: installerName.replace(newerVersion, '99.0.2') }, { name: 'SHA256SUMS-other.txt' }] },
          // A CI prerelease whose tag does not name the installer's version.
          { tag_name: 'polybot-win-ci-999', html_url: 'https://github.com/romanstma-cpu/rom-polybot/releases/tag/polybot-win-ci-999', assets: [{ name: 'ROM.PolyBot-Setup-98.0.0.exe' }] },
          { tag_name: 'v2.35.3', html_url: 'https://github.com/romanstma-cpu/rom-polybot/releases/tag/v2.35.3', assets: [{ name: 'ROM.PolyBot-Setup-2.35.3.exe' }] },
          { tag_name: `v${newerVersion}`, html_url: `https://github.com/romanstma-cpu/rom-polybot/releases/tag/v${newerVersion}`, assets: [{ name: installerName }, { name: checksumName }] },
        ],
      };
    };
  }, { newerVersion: nextVersion, installerName, checksumName });
  const result = await page.evaluate(() => window.rom.app.checkForUpdates());
  assert.equal(result.latestVersion, nextVersion);
  assert.equal(result.updateAvailable, true);
  assert.equal(result.releaseUrl, `https://github.com/romanstma-cpu/rom-polybot/releases/tag/v${nextVersion}`);
  console.log('PASS: update check selects the newest versioned PolyBot release, ignoring other apps and CI prereleases');
} finally {
  await app.evaluate(() => { globalThis.fetch = globalThis.__originalFetch; }).catch(() => {});
  await app.close();
}
