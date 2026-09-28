import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

if (process.platform === 'darwin' && process.arch !== 'arm64') {
  console.log('SKIP: Apple Silicon updater E2E requires an arm64 macOS runner');
  process.exit(0);
}

const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'rom-update-download-'));
const currentVersion = JSON.parse(fs.readFileSync(path.resolve('package.json'), 'utf8')).version;
const [major, minor, patch] = currentVersion.split('.').map(Number);
const version = `${major}.${minor}.${patch + 1}`;
const installerName = process.platform === 'darwin'
  ? `ROM.PolyBot-${version}-arm64.dmg`
  : `ROM.PolyBot-Setup-${version}.exe`;
const checksumName = process.platform === 'darwin'
  ? 'SHA256SUMS-mac-apple-silicon.txt'
  : 'SHA256SUMS-windows-x64.txt';
const installer = Buffer.from(`ROM PolyBot update test fixture ${version}`);
const correctHash = createHash('sha256').update(installer).digest('hex');
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
  await app.evaluate((_electron, fixture) => {
    globalThis.__originalFetch = globalThis.fetch;
    globalThis.__testUpdateHash = '0'.repeat(64);
    globalThis.__testUpdateMode = 'normal';
    const bytes = Buffer.from(fixture.installerBase64, 'base64');
    globalThis.fetch = async (input) => {
      const url = String(input);
      if (url.includes('/releases?per_page=100&page=1')) {
        return new Response(JSON.stringify([{
          tag_name: `v${fixture.version}`,
          assets: [
            { name: fixture.installerName, size: bytes.length },
            { name: fixture.checksumName },
          ],
        }]));
      }
      if (url.endsWith(`/${fixture.checksumName}`)) {
        return new Response(`${globalThis.__testUpdateHash}  ${fixture.installerName}\n`);
      }
      if (url.endsWith(`/${fixture.installerName}`)) {
        if (globalThis.__testUpdateMode === 'slow') {
          return new Response(new ReadableStream({
            start(controller) { controller.enqueue(bytes.subarray(0, 8)); },
          }), { headers: { 'content-length': String(bytes.length) } });
        }
        return new Response(bytes, { headers: { 'content-length': String(bytes.length) } });
      }
      throw new Error(`Unexpected update URL: ${url}`);
    };
  }, {
    version, installerName, checksumName, installerBase64: installer.toString('base64'),
  });

  const check = await page.evaluate(() => window.rom.app.checkForUpdates());
  assert.equal(check.latestVersion, version);
  assert.equal(check.updateAvailable, true);

  // A same-release checksum mismatch must never leave an installer ready.
  await assert.rejects(page.evaluate(() => window.rom.app.downloadUpdate()), /checksum/i);
  let status = await page.evaluate(() => window.rom.app.getUpdateStatus());
  assert.equal(status.phase, 'error');
  assert.match(status.message, /checksum/i);

  await app.evaluate((_electron, hash) => { globalThis.__testUpdateHash = hash; }, correctHash);
  await app.evaluate(() => { globalThis.__testUpdateMode = 'slow'; });
  const pendingDownload = page.evaluate(() => window.rom.app.downloadUpdate().catch((error) => String(error)));
  let sawPartialDownload = false;
  for (let attempt = 0; attempt < 50; attempt++) {
    status = await page.evaluate(() => window.rom.app.getUpdateStatus());
    if (status.phase === 'downloading' && (status.receivedBytes ?? 0) > 0) {
      sawPartialDownload = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(sawPartialDownload, true, 'partial download should be observable before cancellation');
  assert.deepEqual(await page.evaluate(() => window.rom.app.cancelUpdateDownload()), { cancelled: true });
  await pendingDownload;
  status = await page.evaluate(() => window.rom.app.getUpdateStatus());
  assert.equal(status.phase, 'cancelled');
  const userData = await app.evaluate((electronApp) => electronApp.app.getPath('userData'));
  assert.equal(fs.existsSync(path.join(userData, 'updates', version, `${installerName}.partial`)), false);

  await app.evaluate(() => { globalThis.__testUpdateMode = 'normal'; });
  const downloads = await page.evaluate(() => Promise.all([
    window.rom.app.downloadUpdate(),
    window.rom.app.downloadUpdate(),
  ]));
  assert.deepEqual(downloads, [
    { version, ready: true },
    { version, ready: true },
  ]);
  status = await page.evaluate(() => window.rom.app.getUpdateStatus());
  assert.equal(status.phase, 'ready');
  assert.equal(status.receivedBytes, installer.length);

  const saved = fs.readFileSync(path.join(userData, 'updates', version, installerName));
  assert.equal(createHash('sha256').update(saved).digest('hex'), correctHash);

  // An E2E development launch must never execute the test fixture.
  const install = await page.evaluate(() => window.rom.app.installUpdate());
  assert.equal(install.ok, false);
  assert.match(install.message, /packaged app/i);
  console.log('PASS: updater rejects bad checksums, cancels cleanly, retries safely, deduplicates downloads, and keeps installer launch packaged-only');
} finally {
  await app.evaluate(() => { globalThis.fetch = globalThis.__originalFetch; }).catch(() => {});
  await app.close();
}
