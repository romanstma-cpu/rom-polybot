import { _electron as electron } from 'playwright-core';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

if (process.platform !== 'win32') {
  console.log('SKIP: packaged updater handoff requires Windows');
  process.exit(0);
}

const executablePath = path.resolve('release', 'win-unpacked', 'ROM PolyBot.exe');
assert.ok(fs.existsSync(executablePath), `Build the unpacked Windows app first: ${executablePath}`);

const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'rom-update-handoff-'));
const roaming = path.join(sandbox, 'Roaming');
const local = path.join(sandbox, 'Local');
const profile = path.join(sandbox, 'profile');
const sentinel = path.join(sandbox, 'fixture-launched.txt');
for (const directory of [roaming, local, profile]) fs.mkdirSync(directory);

// The updater launches an EXE and quits. This tiny fixture writes only to
// the isolated test directory.
function makeHarmlessInstaller(outputPath) {
  const csharp = `
using System;
using System.IO;
public static class UpdateHandoffFixture {
  public static void Main() {
    File.WriteAllText(${JSON.stringify(sentinel)}, "launched by updater handoff");
  }
}`;
  const scriptPath = path.join(sandbox, 'build-fixture.ps1');
  const quotedOutput = outputPath.replace(/'/g, "''");
  fs.writeFileSync(scriptPath, `$ErrorActionPreference = 'Stop'\n$source = @'\n${csharp}\n'@\nAdd-Type -TypeDefinition $source -OutputAssembly '${quotedOutput}' -OutputType WindowsApplication -ErrorAction Stop\n`);
  const powershell = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const compiled = spawnSync(powershell, [
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', scriptPath,
  ], { encoding: 'utf8', timeout: 30_000, windowsHide: true });
  assert.equal(compiled.status, 0, `Harmless fixture compile failed: ${compiled.stderr || compiled.stdout || compiled.error}`);
  assert.ok(fs.existsSync(outputPath), 'PowerShell did not create the harmless fixture EXE');
}

async function waitUntil(predicate, timeoutMs, description) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${description}`);
}

let app;
let appProcess;
try {
  app = await electron.launch({
    executablePath,
    args: [`--user-data-dir=${profile}`],
    env: { ...process.env, APPDATA: roaming, LOCALAPPDATA: local },
    timeout: 30_000,
  });
  appProcess = app.process();

  const page = await app.firstWindow();
  const appInfo = await app.evaluate((electronModule) => ({
    packaged: electronModule.app.isPackaged,
    version: electronModule.app.getVersion(),
    userData: electronModule.app.getPath('userData'),
  }));
  assert.equal(appInfo.packaged, true, 'handoff must be tested against the packaged app');
  assert.ok(path.resolve(appInfo.userData).toLowerCase().startsWith(path.resolve(sandbox).toLowerCase() + path.sep),
    `Packaged app data escaped the isolated test profile: ${appInfo.userData}`);

  const [major, minor, patch] = appInfo.version.split('.').map(Number);
  assert.ok([major, minor, patch].every(Number.isSafeInteger), `Invalid packaged version: ${appInfo.version}`);
  const version = `${major}.${minor}.${patch + 1}`;
  const installerName = `ROM.PolyBot-Setup-${version}.exe`;
  const checksumName = 'SHA256SUMS-windows-x64.txt';
  const fixturePath = path.join(sandbox, installerName);
  makeHarmlessInstaller(fixturePath);
  const fixture = fs.readFileSync(fixturePath);
  const sha256 = createHash('sha256').update(fixture).digest('hex');
  assert.equal(fs.existsSync(sentinel), false, 'fixture must not run before installUpdate');

  await app.evaluate((_electron, test) => {
    const bytes = Buffer.from(test.fixtureBase64, 'base64');
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (input, init) => {
      const url = String(input);
      if (url === 'https://api.github.com/repos/romanstma-cpu/rom-polybot/releases?per_page=100&page=1') {
        return new Response(JSON.stringify([{
          tag_name: `v${test.version}`,
          draft: false,
          prerelease: false,
          assets: [
            { name: test.installerName, size: bytes.length },
            { name: test.checksumName },
          ],
        }]));
      }
      if (url === `https://github.com/romanstma-cpu/rom-polybot/releases/download/v${test.version}/${test.checksumName}`) {
        return new Response(`${test.sha256}  ${test.installerName}\n`);
      }
      if (url === `https://github.com/romanstma-cpu/rom-polybot/releases/download/v${test.version}/${test.installerName}`) {
        return new Response(bytes, { headers: { 'content-length': String(bytes.length) } });
      }
      return originalFetch(input, init);
    };
  }, {
    version, installerName, checksumName, sha256,
    fixtureBase64: fixture.toString('base64'),
  });

  const config = await page.evaluate(() => window.rom.config.get());
  assert.equal(config.enableTrading, false, 'isolated profile must not have live trading enabled');
  assert.equal(config.crypto15mEnabled, false, 'isolated profile must not have crypto live trading enabled');
  assert.equal(config.scriptsLiveEnabled, false, 'isolated profile must not have script live trading enabled');

  const check = await page.evaluate(() => window.rom.app.checkForUpdates());
  assert.equal(check.latestVersion, version);
  assert.equal(check.updateAvailable, true);
  assert.deepEqual(await page.evaluate(() => window.rom.app.downloadUpdate()), { version, ready: true });
  const downloaded = path.join(appInfo.userData, 'updates', version, installerName);
  assert.equal(createHash('sha256').update(fs.readFileSync(downloaded)).digest('hex'), sha256);

  const exited = new Promise((resolve) => appProcess.once('exit', resolve));
  const install = await page.evaluate(() => window.rom.app.installUpdate());
  assert.equal(install.ok, true, install.message);
  let exitTimer;
  try {
    await Promise.race([
      exited,
      new Promise((_, reject) => {
        exitTimer = setTimeout(() => reject(new Error('Packaged app did not exit after installUpdate')), 20_000);
      }),
    ]);
  } finally {
    clearTimeout(exitTimer);
  }
  await waitUntil(() => fs.existsSync(sentinel), 20_000, 'updater to launch the harmless fixture');
  assert.equal(fs.readFileSync(sentinel, 'utf8'), 'launched by updater handoff');
  console.log('PASS: packaged updater quits and launches a checksum-verified harmless EXE');
} finally {
  if (app) {
    await app.close().catch(() => {});
    if (appProcess?.exitCode === null) appProcess.kill();
  }
  const parent = path.resolve(os.tmpdir()).toLowerCase();
  const resolved = path.resolve(sandbox);
  assert.equal(path.dirname(resolved).toLowerCase(), parent, 'refusing cleanup outside the system temp directory');
  assert.match(path.basename(resolved), /^rom-update-handoff-[A-Za-z0-9]+$/, 'refusing cleanup of an unexpected directory');
  fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
}
