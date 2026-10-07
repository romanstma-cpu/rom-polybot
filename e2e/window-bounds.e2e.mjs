import {_electron as electron} from 'playwright-core';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// A frameless window saved on a monitor that is no longer there used to reopen
// off-screen with no title bar to drag back. Save an off-screen position,
// restart with the same profile, and require the window to open on a display.
// The position is written into the saved settings rather than dragged there:
// Windows may refuse to move a window off a CI runner's only display.
const root=fs.mkdtempSync(path.join(os.tmpdir(),'rom-bounds-'));
for(const dir of ['Roaming','Local'])fs.mkdirSync(path.join(root,dir));
const launch=()=>electron.launch({executablePath:process.env.ROM_E2E_EXE || path.resolve('node_modules/electron/dist/electron.exe'),args:[...(process.env.ROM_E2E_EXE ? [] : [process.cwd()]),`--user-data-dir=${root}/profile`],env:{...process.env,APPDATA:path.join(root,'Roaming'),LOCALAPPDATA:path.join(root,'Local')}});
const windowState=(app)=>app.evaluate(({BrowserWindow,screen})=>{
  const b=BrowserWindow.getAllWindows()[0].getBounds();
  const onScreen=screen.getAllDisplays().some(({workArea:a})=>
    Math.min(b.x+b.width,a.x+a.width)-Math.max(b.x,a.x)>=120 && Math.min(b.y+36,a.y+a.height)-Math.max(b.y,a.y)>=20);
  return {bounds:b,onScreen};
});

const settingsFiles=(dir)=>fs.readdirSync(dir,{withFileTypes:true}).flatMap((e)=>{
  const p=path.join(dir,e.name);
  return e.isDirectory() ? settingsFiles(p) : e.name==='settings.json' ? [p] : [];
});

let app=await launch();
try {
  await app.firstWindow();
  assert.equal((await windowState(app)).onScreen,true,'first launch opens on screen');
} finally {await app.close();}

const saved=settingsFiles(root);
assert.ok(saved.length,'the first launch saved its settings');
for(const file of saved){
  const state=JSON.parse(fs.readFileSync(file,'utf8'));
  fs.writeFileSync(file,JSON.stringify({...state,windowBounds:{x:40000,y:30000,width:1200,height:760}}));
}

app=await launch();
try {
  await app.firstWindow();
  const {bounds,onScreen}=await windowState(app);
  assert.equal(onScreen,true,`reopened off-screen at ${JSON.stringify(bounds)}`);
  // The saved width, fitted to the display, but never under the window's
  // 1100 px minimum (a CI runner's display can be 1024 px wide).
  const workWidth=await app.evaluate(({screen})=>screen.getPrimaryDisplay().workArea.width);
  assert.equal(bounds.width,Math.max(1100,Math.min(1200,workWidth)));
  console.log('PASS: a window saved off-screen reopens on a connected display at its saved size');
} finally {await app.close();}
