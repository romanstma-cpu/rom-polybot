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
    {status && <dl className="mt-5 grid grid-cols-2 gap-3 border-t border-rom-border pt-4 sm:grid-cols-4">
      <div><dt className="text-xs text-rom-muted">Market trades</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{funnel?.tradeEvents?.toLocaleString() ?? '—'}</dd><p className="text-xs text-rom-dim">Rolling 24 hours</p></div>
      <div><dt className="text-xs text-rom-muted">Signals</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{funnel?.signalEvents?.toLocaleString() ?? '—'}</dd><p className="text-xs text-rom-dim">Rolling 24 hours</p></div>
      <div><dt className="text-xs text-rom-muted">Candidates</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{hasCycle ? status.mainCandidates.toLocaleString() : '—'}</dd><p className="text-xs text-rom-dim">{hasCycle ? `Last scan ${fmtRelative(new Date(status.mainLastCycleAt! * 1000).toISOString())}` : 'No scan yet'}</p></div>
      <div><dt className="text-xs text-rom-muted">Entries created</dt><dd className="mt-1 font-mono text-lg font-semibold tabular-nums">{hasCycle ? status.mainPlaced.toLocaleString() : '—'}</dd><p className="text-xs text-rom-dim">Last scan · not fills</p></div>
    </dl>}
    <p className="mt-4 text-xs leading-5 text-rom-dim">These observations explain activity; they do not show that a filtered trade would have been profitable. Practice entries use simulated funds.</p>
  </section>;
}
