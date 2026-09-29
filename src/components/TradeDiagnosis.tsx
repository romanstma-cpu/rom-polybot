import { ArrowRight, SearchCheck } from 'lucide-react';
import type { PageId } from '../App';
import { useApp } from '../state/AppStateProvider';
import { useStrategyActivity } from '../state/StrategyActivity';
import { diagnoseTradeActivity } from '../utils/tradeDiagnosis';
import { cls, fmtRelative } from '../utils/format';

export function TradeDiagnosis({ onNav }: { onNav: (page: PageId) => void }) {
  const { backend, config } = useApp();
  const { status, healthy } = useStrategyActivity();
  const diagnosis = diagnoseTradeActivity(status, {
    backendRunning: backend.status === 'running',
    activityHealthy: healthy,
    apiConnected: backend.authOk,
    scanIntervalSeconds: config?.tradeScanInterval,
  });
  const funnel = status?.opportunityFunnel;
  const decisions = status?.mainDecisions;
  const hasCycle = !!status?.mainLastCycleAt;

  return <section className={cls('rounded-2xl border bg-rom-panel p-5', diagnosis.state === 'blocked' ? 'border-rom-warn/35' : 'border-rom-borderHi')} aria-labelledby="trade-diagnosis-title">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 gap-3">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-rom-purple/30 bg-rom-purple/10 text-rom-purple"><SearchCheck className="h-5 w-5" /></div>
        <div>
          <h3 id="trade-diagnosis-title" className="font-semibold">Why am I not seeing trades?</h3>
          <p className="mt-1 text-sm font-semibold text-white" role="status">{diagnosis.title}</p>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-rom-muted">{diagnosis.detail}</p>
        </div>
      </div>
      <button className="rom-btn-default shrink-0" onClick={() => onNav(diagnosis.destination)}>{diagnosis.action}<ArrowRight className="h-4 w-4" /></button>
    </div>
    {status && <dl className="mt-5 grid grid-cols-2 gap-3 border-t border-rom-border pt-4 sm:grid-cols-3 xl:grid-cols-5">
      <div><dt className="text-xs text-rom-muted">Markets watched</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{funnel?.watchedMarkets?.toLocaleString() ?? '—'}</dd><p className="text-xs text-rom-dim">Current US feed</p></div>
      <div><dt className="text-xs text-rom-muted">Market trades</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{funnel?.tradeEvents?.toLocaleString() ?? '—'}</dd><p className="text-xs text-rom-dim">Rolling 24 hours</p></div>
      <div><dt className="text-xs text-rom-muted">Signals</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{funnel?.signalEvents?.toLocaleString() ?? '—'}</dd><p className="text-xs text-rom-dim">Rolling 24 hours</p></div>
      <div><dt className="text-xs text-rom-muted">Candidates</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{hasCycle ? status.mainCandidates.toLocaleString() : '—'}</dd><p className="text-xs text-rom-dim">{hasCycle ? `Last scan ${fmtRelative(new Date(status.mainLastCycleAt! * 1000).toISOString())}` : 'No scan yet'}</p></div>
      <div><dt className="text-xs text-rom-muted">Entries created</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{hasCycle ? status.mainPlaced.toLocaleString() : '—'}</dd><p className="text-xs text-rom-dim">Last scan · not fills</p></div>
    </dl>}
    {decisions && <details className="mt-4 rounded-xl border border-rom-border bg-rom-void/35 px-4 py-3">
      <summary className="cursor-pointer text-sm font-medium text-white">Decision history · last 24 hours <span className="ml-2 text-xs font-normal text-rom-muted">{(decisions.totals.skipped || 0).toLocaleString()} skipped · {(decisions.totals.practice_fill || 0).toLocaleString()} Practice fills · {(decisions.totals.submitted || 0).toLocaleString()} orders submitted · {((decisions.totals.rejected || 0) + (decisions.totals.unknown || 0) + (decisions.totals.error || 0)).toLocaleString()} need review</span></summary>
      <div className="mt-4 grid gap-5 border-t border-rom-border pt-4 lg:grid-cols-2">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-rom-muted">Most common stops</h4>
          {decisions.topSkipped.length ? <ul className="mt-2 space-y-2">{decisions.topSkipped.map((item, index) => <li key={`${item.stage}-${item.reason}-${index}`} className="flex items-start justify-between gap-3 text-xs"><span className="min-w-0 break-words text-rom-muted"><span className="mr-2 uppercase text-rom-purple">{item.stage}</span>{item.reason}</span><strong className="shrink-0 font-mono text-white">{item.count}</strong></li>)}</ul> : <p className="mt-2 text-xs text-rom-dim">No skipped decisions recorded yet.</p>}
        </div>
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-rom-muted">Latest decisions</h4>
          {decisions.recent.length ? <ul className="mt-2 space-y-2">{decisions.recent.map((item, index) => <li key={`${item.trace_id}-${item.signal_id}-${index}`} className="rounded-lg border border-rom-border bg-rom-panel/50 p-2 text-xs"><div className="flex flex-wrap items-center justify-between gap-2"><span className="min-w-0 break-all font-mono text-white">{item.ticker || 'Main scan'}</span><span className="text-rom-dim">{fmtRelative(new Date(item.at * 1000).toISOString())}</span></div><p className="mt-1 break-words text-rom-muted"><span className="capitalize text-rom-purple">{item.mode} · {item.stage} · {item.outcome.replace('_', ' ')}</span>{item.positionStatus ? ` · position ${item.positionStatus}` : ''} — {item.reason}</p></li>)}</ul> : <p className="mt-2 text-xs text-rom-dim">Start Practice to record scan decisions. No account funds are required.</p>}
        </div>
      </div>
      <p className="mt-4 text-xs leading-5 text-rom-dim">A submitted order is not a fill. Position status updates as the exchange confirms it. Repeated identical cycle blockers are saved once per minute; candidate decisions are saved individually. This journal is diagnostic only.</p>
    </details>}
    <p className="mt-4 text-xs leading-5 text-rom-dim">These observations explain activity; they do not show that a filtered trade would have been profitable. Practice entries use simulated funds.</p>
  </section>;
}
