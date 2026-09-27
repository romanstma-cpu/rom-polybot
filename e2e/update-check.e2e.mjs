import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'rom-updates-'));
const currentVersion = JSON.parse(fs.readFileSync(path.resolve('package.json'), 'utf8')).version;
const [major, minor, patch] = currentVersion.split('.').map(Number);
const nextVersion = `${major}.${minor}.${patch + 1}`;
for (const dir of ['Roaming', 'Local', 'profile']) fs.mkdirSync(path.join(sandbox, dir));
const app = await electron.launch({
  executablePath: path.resolve('node_modules', 'electron', 'dist',
    process.platform === 'win32' ? 'electron.exe' : 'electron'),
  args: [process.cwd(), `--user-data-dir=${path.join(sandbox, 'profile')}`],
  env: { ...process.env, APPDATA: path.join(sandbox, 'Roaming'), LOCALAPPDATA: path.join(sandbox, 'Local') },
  timeout: 30_000,
});

try {
  const page = await app.firstWindow();
  await app.evaluate((_electron, newerVersion) => {
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
          // A CI prerelease whose tag does not name the installer's version.
          { tag_name: 'polybot-win-ci-999', html_url: 'https://github.com/romanstma-cpu/rom-polybot/releases/tag/polybot-win-ci-999', assets: [{ name: 'ROM.PolyBot-Setup-98.0.0.exe' }] },
          { tag_name: 'v2.35.3', html_url: 'https://github.com/romanstma-cpu/rom-polybot/releases/tag/v2.35.3', assets: [{ name: 'ROM.PolyBot-Setup-2.35.3.exe' }] },
          { tag_name: `v${newerVersion}`, html_url: `https://github.com/romanstma-cpu/rom-polybot/releases/tag/v${newerVersion}`, assets: [{ name: `ROM.PolyBot-Setup-${newerVersion}.exe` }, { name: `ROM.PolyBot-${newerVersion}-arm64.dmg` }] },
        ],
      };
    };
  }, nextVersion);
  const result = await page.evaluate(() => window.rom.app.checkForUpdates());
  assert.equal(result.latestVersion, nextVersion);
  assert.equal(result.updateAvailable, true);
  assert.equal(result.releaseUrl, `https://github.com/romanstma-cpu/rom-polybot/releases/tag/v${nextVersion}`);
  console.log('PASS: update check selects the newest versioned PolyBot release, ignoring other apps and CI prereleases');
} finally {
  await app.evaluate(() => { globalThis.fetch = globalThis.__originalFetch; }).catch(() => {});
  await app.close();
}
