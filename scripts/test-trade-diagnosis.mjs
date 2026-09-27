import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformSync } from 'esbuild';

const source = readFileSync(new URL('../src/utils/tradeDiagnosis.ts', import.meta.url), 'utf8');
const { code } = transformSync(source, { loader: 'ts', format: 'esm' });
const { diagnoseTradeActivity } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

const nowMs = 1_800_000_000_000;
const context = { backendRunning: true, activityHealthy: true, apiConnected: true, nowMs };
const base = {
  mainMode: 'paper', mainState: 'scanning', mainSummary: 'Scanning',
  main: [], mainLastCycleAt: nowMs / 1000 - 10,
  mainCandidates: 0, mainPlaced: 0, mainFilterCounts: {},
  opportunityFunnel: { tradeEvents: 12, signalEvents: 0, primaryBlock: '' },
};
const explain = (status, extra = {}) => diagnoseTradeActivity({ ...base, ...status }, { ...context, ...extra });

assert.equal(explain({ mainMode: 'paused' }, { apiConnected: false }).title, 'Trading is paused');
assert.equal(explain({}, { apiConnected: false }).destination, 'api');
assert.equal(explain({ mainMode: 'live', main: [{ id: 'buyingPower', state: 'blocked', label: 'Available USD buying power', reason: 'No available USD buying power' }] }).detail, 'No available USD buying power');
assert.equal(explain({ mainMode: 'live', main: [{ id: 'qualifiedEdge', state: 'blocked', label: 'Qualified live signal evidence', reason: 'No qualified group' }] }).destination, 'evidence');
assert.equal(explain({ mainPlaced: 1, mainLastCycleAt: nowMs / 1000 - 180 }).title, 'No recent decision cycle');
assert.match(explain({ mainPlaced: 1, mainMode: 'live' }).detail, /Check positions for fill/);
assert.equal(explain({ mainFilterCounts: { 'price too high': 3 }, opportunityFunnel: { tradeEvents: 12, signalEvents: 4, primaryBlock: '' } }).title, 'Candidates failed an entry check');
assert.equal(explain({}).title, 'Market trades have not formed a signal');
assert.equal(diagnoseTradeActivity(null, { ...context, backendRunning: false }).title, 'The engine is offline');

console.log('PASS: paused, auth, live gates, stale scan, created orders, filters, signals, and offline diagnosis');
