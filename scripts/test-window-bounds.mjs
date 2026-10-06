/**
 * Unit tests for electron/system/window-bounds.ts.
 * Same pattern as scripts/test-config-validate.mjs: transpile, load in a vm
 * context, assert. Run: node scripts/test-window-bounds.mjs
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import vm from 'node:vm';

const output = ts.transpileModule(fs.readFileSync(path.resolve('electron/system/window-bounds.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = { exports: {} };
vm.runInNewContext(output, context);
const { restoreBounds } = context.exports;
const plain = (value) => JSON.parse(JSON.stringify(value));

const laptop = { x: 0, y: 0, width: 1536, height: 816 };
const monitorRight = { x: 1536, y: 0, width: 2560, height: 1400 };
const monitorLeft = { x: -1920, y: 0, width: 1920, height: 1040 };

// First launch: a default size, centred by Electron.
assert.deepEqual(plain(restoreBounds(null, [laptop], laptop)), { width: 1380, height: 776 });

// Saved on a display that is still connected: keep position and size.
assert.deepEqual(
  plain(restoreBounds({ x: 1700, y: 80, width: 1400, height: 900 }, [laptop, monitorRight], laptop)),
  { x: 1700, y: 80, width: 1400, height: 900 });
assert.deepEqual(
  plain(restoreBounds({ x: -1800, y: 40, width: 1300, height: 800 }, [monitorLeft, laptop], laptop)),
  { x: -1800, y: 40, width: 1300, height: 800 });

// That monitor was unplugged: open on the primary display, sized to fit it.
assert.deepEqual(
  plain(restoreBounds({ x: 1700, y: 80, width: 2400, height: 1300 }, [laptop], laptop)),
  { width: 1536, height: 816 });
assert.deepEqual(
  plain(restoreBounds({ x: -1800, y: 40, width: 1300, height: 800 }, [laptop], laptop)),
  { width: 1300, height: 800 });

// Only a sliver of the title bar would be on screen: not enough to drag it.
assert.deepEqual(
  plain(restoreBounds({ x: 1450, y: 100, width: 1200, height: 700 }, [laptop], laptop)),
  { width: 1200, height: 700 });
// The title bar sits above the top of the work area.
assert.deepEqual(
  plain(restoreBounds({ x: 100, y: -30, width: 1200, height: 700 }, [laptop], laptop)),
  { width: 1200, height: 700 });
// Enough of it is visible to grab: keep the position.
assert.deepEqual(
  plain(restoreBounds({ x: 1300, y: 100, width: 1200, height: 700 }, [laptop], laptop)),
  { x: 1300, y: 100, width: 1200, height: 700 });

// Corrupt or partial saved bounds fall back to the default.
for (const bad of [{}, { width: NaN, height: 700 }, { width: 0, height: 700 }, { width: 1200, height: -5 }]) {
  assert.deepEqual(plain(restoreBounds(bad, [laptop], laptop)), { width: 1380, height: 776 });
}
// A size with no position opens centred.
assert.deepEqual(plain(restoreBounds({ width: 1200, height: 700 }, [laptop], laptop)), { width: 1200, height: 700 });

console.log('PASS: window bounds restore on connected displays, recentre when off-screen, fit the work area');
