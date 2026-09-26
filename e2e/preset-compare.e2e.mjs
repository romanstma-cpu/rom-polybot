// The Backtest page asks the live evidence gate about each crypto preset.
// Drives the real backend: every row must come back with the gate's verdict.
import {_electron as electron} from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rom-preset-compare-'));
for (const d of ['Roaming', 'Local', 'profile']) fs.mkdirSync(path.join(root, d));
const app = await electron.launch({
  executablePath: path.resolve('node_modules', 'electron', 'dist',
    process.platform === 'win32' ? 'electron.exe' : 'electron'),
  args: [process.cwd(), `--user-data-dir=${path.join(root, 'profile')}`],
  env: {...process.env, APPDATA: path.join(root, 'Roaming'), LOCALAPPDATA: path.join(root, 'Local')},
  timeout: 60000,
});
try {
  const page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.getByRole('button', {name: 'Continue to API setup'}).click();
  await page.getByRole('button', {name: /Advanced tools/}).click();
  await page.getByRole('navigation').getByRole('button', {name: 'Backtest', exact: true}).click();
  await page.getByRole('button', {name: 'Crypto up/down', exact: true}).click();
  await page.getByRole('button', {name: 'Compare presets on my data'}).click();
  // The last preset's row, then the button back from "Comparing…".
  await page.getByRole('cell', {name: 'Contrarian fade'}).waitFor({timeout: 90000});
  await page.getByRole('button', {name: 'Compare presets on my data'}).waitFor({timeout: 90000});
  const rows = await page.locator('table tbody tr').allInnerTexts();
  assert.equal(rows.length, 6, rows.join('\n'));
  // A fresh profile has recorded nothing, so no preset can be proven.
  assert.ok(rows.every((r) => r.includes('not enough evidence')), rows.join('\n'));
  assert.deepEqual(errors, []);
  console.log('PASS: every crypto preset gets the live evidence gate\'s verdict');
} finally { await app.close(); }
