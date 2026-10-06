import {_electron as electron} from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// A frameless window saved on a monitor that is no longer there used to reopen
// off-screen with no title bar to drag back. Save an off-screen position,
// restart with the same profile, and require the window to open on a display.
const root=fs.mkdtempSync(path.join(os.tmpdir(),'rom-bounds-'));
for(const dir of ['Roaming','Local'])fs.mkdirSync(path.join(root,dir));
const launch=()=>electron.launch({executablePath:process.env.ROM_E2E_EXE || path.resolve('node_modules/electron/dist/electron.exe'),args:[...(process.env.ROM_E2E_EXE ? [] : [process.cwd()]),`--user-data-dir=${root}/profile`],env:{...process.env,APPDATA:path.join(root,'Roaming'),LOCALAPPDATA:path.join(root,'Local')}});
const windowState=(app)=>app.evaluate(({BrowserWindow,screen})=>{
  const b=BrowserWindow.getAllWindows()[0].getBounds();
  const onScreen=screen.getAllDisplays().some(({workArea:a})=>
    Math.min(b.x+b.width,a.x+a.width)-Math.max(b.x,a.x)>=120 && Math.min(b.y+36,a.y+a.height)-Math.max(b.y,a.y)>=20);
  return {bounds:b,onScreen};
});

let app=await launch();
try {
  await app.firstWindow();
  assert.equal((await windowState(app)).onScreen,true,'first launch opens on screen');
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setBounds({x:40000,y:30000,width:1200,height:760}));
  await new Promise(resolve=>setTimeout(resolve,1200)); // bounds are saved 500 ms after a move
  assert.equal((await windowState(app)).onScreen,false,'the fixture really moved the window off-screen');
} finally {await app.close();}

app=await launch();
try {
  await app.firstWindow();
  const {bounds,onScreen}=await windowState(app);
  assert.equal(onScreen,true,`reopened off-screen at ${JSON.stringify(bounds)}`);
  assert.equal(bounds.width,1200);
  console.log('PASS: a window saved off-screen reopens on a connected display at its saved size');
} finally {await app.close();}
