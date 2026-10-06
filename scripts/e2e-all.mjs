// Run every Electron end-to-end suite in e2e/, one at a time.
//
// Suites that nothing ran drifted: five of them failed against 2.36.6 (one
// because of a real Evidence crash) while every release gate passed. The
// Windows release gate now runs them all. A failing suite is retried once,
// as the UI audit is, so a transient Electron start-up timeout does not block
// a release; a suite that fails twice does.
//
//   npm run test:e2e:all            every suite
//   npm run test:e2e:all -- kill    only suites whose file name contains "kill"
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const filters = process.argv.slice(2);
// These drive the packaged app, which the release workflow tests after it
// has built the installer.
const PACKAGED_ONLY = new Set(['update-handoff.e2e.mjs']);
const suites = fs.readdirSync(path.resolve('e2e'))
  .filter((name) => name.endsWith('.e2e.mjs') && !PACKAGED_ONLY.has(name))
  .filter((name) => !filters.length || filters.some((f) => name.includes(f)))
  .sort();
if (!suites.length) {
  console.error(`No e2e suite matches ${filters.join(', ')}`);
  process.exit(1);
}

const run = (suite) => spawnSync(process.execPath, [path.join('e2e', suite)], {
  cwd: process.cwd(),
  env: process.env,
  stdio: 'inherit',
  timeout: 5 * 60_000,
  windowsHide: true,
});

const failed = [];
const started = Date.now();
for (const suite of suites) {
  console.log(`\n>> e2e: ${suite}`);
  let result = run(suite);
  if (result.status !== 0) {
    console.warn(`\n${suite} failed (${result.error?.message || `exit ${result.status}`}); retrying once.`);
    result = run(suite);
  }
  if (result.status !== 0) failed.push(suite);
}

const minutes = ((Date.now() - started) / 60_000).toFixed(1);
if (failed.length) {
  console.error(`\n>> ${failed.length} of ${suites.length} e2e suites failed in ${minutes} min: ${failed.join(', ')}`);
  process.exit(1);
}
console.log(`\n>> All ${suites.length} e2e suites passed in ${minutes} min`);
