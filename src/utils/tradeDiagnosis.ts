import type { TradingStatus } from '@shared/types';

type Destination = 'main' | 'api' | 'evidence' | 'positions';

export interface TradeDiagnosis {
  title: string;
  detail: string;
  action: string;
  destination: Destination;
  state: 'waiting' | 'blocked' | 'activity';
}

/** Explain the first observable obstacle without inferring that a rejected trade would win. */
export function diagnoseTradeActivity(
  status: TradingStatus | null,
  { backendRunning, activityHealthy, apiConnected, scanIntervalSeconds = 30, nowMs = Date.now() }: {
    backendRunning: boolean;
    activityHealthy: boolean;
    apiConnected: boolean;
    scanIntervalSeconds?: number;
    nowMs?: number;
  },
): TradeDiagnosis {
  if (!backendRunning) return {
    title: 'The engine is offline',
    detail: 'Start the engine to scan markets and record decision cycles.',
    action: 'Open strategy', destination: 'main', state: 'blocked',
  };
  if (!activityHealthy || !status) return {
    title: 'Waiting for a reliable engine update',
    detail: 'The latest activity could not be confirmed. Check the engine connection before trusting older counts.',
    action: 'Open strategy', destination: 'main', state: 'waiting',
  };
  if (status.mainMode === 'paused') return {
    title: 'Trading is paused',
    detail: 'Start Practice to observe simulated entries, or review the live requirements before enabling orders.',
    action: 'Choose a mode', destination: 'main', state: 'waiting',
  };
  if (!apiConnected) return {
    title: 'Polymarket US API is disconnected',
    detail: 'Reconnect your API credentials so Polybot can read the market and your account.',
    action: 'Check API', destination: 'api', state: 'blocked',
  };
  if (status.recovery?.blocked) return {
    title: 'An order needs reconciliation',
    detail: 'New submissions are halted until the uncertain order outcome is resolved in the recovery panel above.',
    action: 'Review positions', destination: 'positions', state: 'blocked',
  };

  const blockedGate = status.main.find(gate => gate.state === 'blocked' && gate.id !== 'cycle');
  if (blockedGate) return {
    title: blockedGate.label,
    detail: blockedGate.reason || 'This requirement is stopping new entries.',
    action: blockedGate.id === 'auth' ? 'Check API'
      : blockedGate.id === 'qualifiedEdge' ? 'Review evidence' : 'Review strategy',
    destination: blockedGate.id === 'auth' ? 'api'
      : blockedGate.id === 'qualifiedEdge' ? 'evidence' : 'main',
    state: 'blocked',
  };

  const funnel = status.opportunityFunnel;
  const latestCycleAgeMs = status.mainLastCycleAt
    ? nowMs - status.mainLastCycleAt * 1000
    : null;
  if (latestCycleAgeMs === null || latestCycleAgeMs > Math.max(60_000, scanIntervalSeconds * 3_000)) return {
    title: latestCycleAgeMs === null ? 'No decision cycle confirmed yet' : 'No recent decision cycle',
    detail: latestCycleAgeMs === null
      ? 'The engine has not reported a completed scan. Feed counts alone do not mean an order was evaluated.'
      : 'The last reported scan is too old to describe current activity. Check the engine and market feed.',
    action: 'Review strategy', destination: 'main', state: 'waiting',
  };
  if (status.mainPlaced > 0) return {
    title: status.mainMode === 'paper' ? 'Practice entries were created' : 'Orders were created',
    detail: `The latest scan created ${status.mainPlaced} ${status.mainMode === 'paper' ? 'simulated entry' : 'order'}${status.mainPlaced === 1 ? '' : 's'}. Check positions for fill and settlement status.`,
    action: 'View positions', destination: 'positions', state: 'activity',
  };
  if (funnel?.primaryBlock) return {
    title: 'Latest scan was blocked',
    detail: funnel.primaryBlock,
    action: 'Review evidence', destination: 'evidence', state: 'waiting',
  };
  const leadingFilter = Object.entries(status.mainFilterCounts || {})
    .sort((a, b) => b[1] - a[1])[0];
  if (leadingFilter && leadingFilter[1] > 0) return {
    title: 'Candidates failed an entry check',
    detail: `${leadingFilter[0]} was the leading filter in the latest scan (${leadingFilter[1]}).`,
    action: 'Review evidence', destination: 'evidence', state: 'waiting',
  };
  if (funnel?.tradeEvents === 0) return {
    title: 'No market trades recorded in 24 hours',
    detail: 'The scanner has not recorded a public trade in the rolling feed window. Check market coverage and the connection.',
    action: 'Review strategy', destination: 'main', state: 'waiting',
  };
  if (funnel?.signalEvents === 0) return {
    title: 'Market trades have not formed a signal',
    detail: 'Trade prints arrived, but none met the scanner’s signal conditions in the rolling 24-hour window.',
    action: 'Review evidence', destination: 'evidence', state: 'waiting',
  };
  if (status.mainCandidates === 0) return {
    title: 'No candidate in the latest scan',
    detail: 'Signals were recorded in the last 24 hours, but this scan found no current candidate to evaluate.',
    action: 'Review evidence', destination: 'evidence', state: 'waiting',
  };
  return {
    title: 'Candidates checked; no entry created',
    detail: status.mainSummary || 'The latest candidates did not pass all current entry checks.',
    action: 'Review evidence', destination: 'evidence', state: 'waiting',
  };
}
