import {_electron as electron} from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'rom-graphics-'));
for (const dir of ['Roaming', 'Local']) fs.mkdirSync(path.join(sandbox, dir));
const out = path.resolve('.work/graphics');
fs.mkdirSync(out, {recursive: true});
const app = await electron.launch({
  executablePath: path.resolve('node_modules/electron/dist/electron.exe'),
  args: [process.cwd(), `--user-data-dir=${sandbox}/profile`],
  env: {...process.env, APPDATA: `${sandbox}/Roaming`, LOCALAPPDATA: `${sandbox}/Local`},
});
try {
  const page = await app.firstWindow();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.getByRole('button', {name: 'Continue to API setup'}).click();
  await page.getByRole('button', {name: /Advanced tools/}).click();
  for (const width of [1440, 1000, 700]) {
    await app.evaluate(({BrowserWindow}, width) => {
      const win = BrowserWindow.getAllWindows()[0];
      win.setMinimumSize(620, 500);
      win.setSize(width, 950);
    }, width);
    for (const name of ['Terminal', 'Live Visualizer']) {
      await page.getByRole('navigation').getByRole('button', {name, exact: true}).click();
      await page.waitForTimeout(700);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      assert.equal(await page.evaluate(() => [...document.querySelectorAll('.kt-root,.visualizer-workspace')]
        .some(el => el.scrollWidth > el.clientWidth + 1)), false, `${name} internal overflow at ${width}`);
      assert.ok(await page.evaluate(width => innerWidth <= width, width), 'requested viewport applied');
      const canvas = page.locator(name === 'Terminal' ? '.kt-field canvas' : '.orbital-stage canvas');
      const box = await canvas.boundingBox();
      assert.ok(box && box.width > 200 && box.height >= 400, `${name} readable canvas at ${width}`);
      await page.screenshot({path: path.join(out, `${name.replaceAll(' ', '-')}-${width}.png`)});
    }
  }
  await page.getByRole('button', {name: 'Pause', exact: true}).click();
  await page.getByText('Animation paused', {exact: false}).waitFor();
  await page.getByRole('button', {name: 'Resume', exact: true}).click();
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.getByRole('navigation').getByRole('button', {name: 'Terminal', exact: true}).click();
  await page.getByText('FLOW ANIMATION · NOT FILLS', {exact: true}).waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS: Terminal and Visualizer render at 1440/1000/700px, animation controls work, reduced motion renders without errors');
} finally {
  await app.close();
}
