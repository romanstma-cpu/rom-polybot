import {_electron as electron} from 'playwright-core';
import assert from 'node:assert/strict';
import {backendRunning} from './wait.mjs';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
const root=fs.mkdtempSync(path.join(os.tmpdir(),'rom-public-'));
for(const d of ['Roaming','Local'])fs.mkdirSync(path.join(root,d));
const app=await electron.launch({executablePath:path.resolve('node_modules/electron/dist/electron.exe'),args:[process.cwd(),`--user-data-dir=${root}/profile`],env:{...process.env,APPDATA:path.join(root,'Roaming'),LOCALAPPDATA:path.join(root,'Local')}});
try{
 const p=await app.firstWindow();const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.getByRole('button',{name:'Continue to API setup'}).click();
 await p.getByRole('navigation').getByRole('button',{name:'Overview',exact:true}).click();
 await p.waitForTimeout(5500);
 await p.getByRole('heading',{name:'Trade readiness',exact:true}).waitFor();
 assert.ok(await p.getByText('Execution guard',{exact:true}).count());
 await p.screenshot({path:'.work/overview-2.7.png'});
 await p.getByRole('navigation').getByRole('button',{name:'Strategy',exact:true}).click();
 // Let the real backend finish starting first: a status it reports after the
 // fixtures below would replace them and leave Start live disabled.
 await backendRunning(p,90000);
 // Start live also needs qualified evidence and buying power. Supply both as
 // fixtures so the review dialog itself is what this suite exercises.
 await app.evaluate(({ipcMain,BrowserWindow})=>{
  ipcMain.removeHandler('data:account');
  ipcMain.handle('data:account',()=>({cashUsd:25,portfolioUsd:0,totalUsd:25,startBankrollUsd:25,roiPct:0,realizedPnlUsd:0,unrealizedPnlUsd:0,openCostUsd:0,feesUsd:0,wins:0,losses:0,winRate:0,pendingCount:0,openCount:0,resolvedCount:0,totalOpened:0,byNetwork:{mainnet:{wins:0,losses:0,realizedPnl:0}}}));
  ipcMain.removeHandler('trading:status');
  ipcMain.handle('trading:status',()=>({main:[{id:'qualifiedEdge',label:'Qualified live evidence',state:'ok',reason:''}],mainMode:'off',mainState:'paused',mainSummary:'Fixture paused',mainLastCycleAt:Date.now()/1000,mainFilterCounts:{},mainCandidates:0,mainPlaced:0,mainPaper:{bankrollUsd:1000,availableUsd:1000,pnlUsd:0,open:0,resolved:0},executionHealth:{state:'closed',blocked:false,reason:'',marketStream:{state:'connected',connected:true,watchedMarkets:1}}}));
  const win=BrowserWindow.getAllWindows()[0].webContents;
  win.send('data:reset',{});
  win.send('backend:info',{status:'running',authOk:true,pid:1,pythonOk:true,startedAt:null,lastError:null});
 });
 await p.getByRole('button',{name:'Start live',exact:true}).click();
 await p.getByRole('dialog',{name:'Review before going live'}).waitFor();
 assert.equal(await p.getByRole('button',{name:'Close live review'}).evaluate(e=>e===document.activeElement),true);
 assert.equal(await p.getByRole('button',{name:'Enable live trading'}).isDisabled(),true);
 await p.getByText(/I am choosing to continue without a completed practice trade/).click();
 await p.getByText(/I understand this can place real orders and lose money/).click();
 assert.equal(await p.getByRole('button',{name:'Enable live trading'}).isDisabled(),false);
 await p.screenshot({path:'.work/live-review-2.7.png'});
 await app.evaluate(({BrowserWindow})=>{BrowserWindow.getAllWindows()[0].webContents.send('backend:info',{status:'stopped',authOk:false,pid:null,pythonOk:false,startedAt:null,lastError:null});});
 assert.equal(await p.getByRole('button',{name:'Enable live trading'}).isDisabled(),true);
 await p.keyboard.press('Escape');
 assert.equal(await p.getByRole('dialog').count(),0);
 assert.equal((await p.evaluate(()=>window.rom.config.get())).enableTrading,false);
 assert.deepEqual(errors,[]);
 console.log('PASS: review focus, connection-loss guard, Escape, no trading enabled, clean renderer');
}finally{await app.close();}
