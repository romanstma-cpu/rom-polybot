import {_electron as electron} from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {backendRunning} from './wait.mjs';

// Reopening Evidence inside the backend's five-minute report cache used to
// crash the whole window, and any page failure replaced the app (sidebar and
// emergency stop included) with a reload screen. Both must stay contained.
const root=fs.mkdtempSync(path.join(os.tmpdir(),'rom-resilience-'));
for(const dir of ['Roaming','Local'])fs.mkdirSync(path.join(root,dir));
const app=await electron.launch({executablePath:process.env.ROM_E2E_EXE || path.resolve('node_modules/electron/dist/electron.exe'),args:[...(process.env.ROM_E2E_EXE ? [] : [process.cwd()]),`--user-data-dir=${root}/profile`],env:{...process.env,APPDATA:path.join(root,'Roaming'),LOCALAPPDATA:path.join(root,'Local')}});
try {
  const page=await app.firstWindow();
  const nav=(name)=>page.getByRole('navigation').getByRole('button',{name,exact:true}).click();
  await page.getByRole('button',{name:'Continue to API setup'}).click();
  await backendRunning(page);
  await page.getByRole('button',{name:'Advanced tools'}).click();

  // Open Evidence, leave, and come back while the reports are still cached.
  for(let visit=0;visit<2;visit++){
    await nav('Evidence');
    await page.getByText('Execution intelligence lab',{exact:true}).waitFor();
    await page.getByText('Evaluating confirmed order and markout evidence…',{exact:true}).waitFor({state:'detached'});
    await nav('Overview');
  }
  await nav('Evidence');
  const refresh=page.getByRole('button',{name:'Refresh evidence',exact:true});
  await refresh.click();
  await page.getByText('Evaluating confirmed order and markout evidence…',{exact:true}).waitFor({state:'detached'});
  assert.equal(await page.getByText('This page ran into a problem',{exact:true}).count(),0);
  assert.equal(await page.getByText('This screen ran into a problem',{exact:true}).count(),0);

  // A malformed report now fails one page, not the window.
  await app.evaluate(({ipcMain})=>{
    ipcMain.removeHandler('trading:executionShadow');
    ipcMain.handle('trading:executionShadow',()=>({status:'collecting',reason:'Malformed test report'}));
  });
  await refresh.click();
  await page.getByText('This page ran into a problem',{exact:true}).waitFor();
  assert.equal(await page.getByText('This screen ran into a problem',{exact:true}).count(),0);
  await nav('Overview');
  await page.getByRole('heading',{name:'Overview',exact:true}).waitFor();
  assert.equal(await page.getByText('This page ran into a problem',{exact:true}).count(),0);
  assert.equal((await page.evaluate(()=>window.rom.config.get())).enableTrading,false);
  console.log('PASS: Evidence reopens from cache, a broken page stays contained and navigation keeps working; no trading enabled');
} finally {await app.close();}
