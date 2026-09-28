import { useEffect, useState } from 'react';
import { StrategyActivityProvider } from './state/StrategyActivity';
import { TitleBar } from './components/TitleBar';
import { Sidebar } from './components/Sidebar';
import { WorkspaceStatus } from './components/WorkspaceStatus';
import { AppStateProvider, useApp } from './state/AppStateProvider';
import { ToastProvider } from './state/ToastProvider';
import { UpdateProvider, useUpdates } from './state/UpdateProvider';
import { OnboardingModal } from './pages/Onboarding';
import { DashboardPage } from './pages/Dashboard';
import { OverviewPage } from './pages/Overview';
import { EvidencePage } from './pages/Evidence';
import { MainEnginePage } from './pages/MainEngine';
import { SettingsPage } from './pages/Settings';
import { PositionsPage } from './pages/Positions';
import { SignalsPage } from './pages/Signals';
import { HistoryPage } from './pages/History';
import { ProfilesPage } from './pages/Profiles';
import { LogsPage } from './pages/Logs';
import { ApiKeysPage } from './pages/ApiKeys';
import { AboutPage } from './pages/About';
import { GuidePage } from './pages/Guide';
import { VisualizerPage } from './pages/Visualizer';
import { Crypto15mPage } from './pages/Crypto15m';
import { AccountsPage } from './pages/Accounts';
import { BacktestPage } from './pages/Backtest';
import { TerminalPage } from './pages/Terminal';
import { ScriptsPage } from './pages/Scripts';

export type PageId =
  | 'dashboard' | 'main' | 'positions' | 'signals' | 'history'
  | 'profiles' | 'settings' | 'api' | 'logs' | 'guide' | 'about'
  | 'visualizer' | 'crypto15m' | 'accounts' | 'backtest'
  | 'terminal' | 'scripts' | 'analytics' | 'evidence';

const PAGE_SHORTCUTS: Record<string, PageId> = {
  '1': 'dashboard',
  '2': 'main',
  '3': 'positions',
  '4': 'signals',
  '5': 'history',
  '6': 'crypto15m',
  '7': 'backtest',
  '8': 'settings',
  '9': 'api',
};

export default function App() {
  return (
    <ToastProvider>
      <UpdateProvider>
        <AppStateProvider>
          <StrategyActivityProvider><Shell /></StrategyActivityProvider>
        </AppStateProvider>
      </UpdateProvider>
    </ToastProvider>
  );
}

function Shell() {
  const [page, setPage] = useState<PageId>('dashboard');
  const { state } = useApp();

  // Keyboard navigation: Ctrl+1..9 jumps to a page; Ctrl+K is a 2-key
  // press (K then a digit) to avoid hijacking browser find/save.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (e.isComposing || target?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"], dialog') || document.querySelector('[aria-modal="true"], dialog[open]')) return;
      if (e.ctrlKey || e.metaKey) {
        const t = e.key.toLowerCase();
        if (PAGE_SHORTCUTS[t]) {
          e.preventDefault();
          setPage(PAGE_SHORTCUTS[t]);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const showOnboarding = state ? !state.acceptedDisclaimer : false;

  return (
    <div className="app-shell flex h-full w-full flex-col bg-rom-void">
      <TitleBar />
      <div className="flex min-h-0 flex-1 w-full">
        <Sidebar page={page} setPage={setPage} />
        <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
          <WorkspaceStatus />
          <UpdateNotice onOpen={() => setPage('settings')} />
          <div className="min-h-0 flex-1 overflow-hidden bg-rom-radial-r">
            <PageRouter page={page} setPage={setPage} />
          </div>
        </main>
      </div>
      {showOnboarding && <OnboardingModal onDone={() => setPage('api')} />}
    </div>
  );
}

function UpdateNotice({ onOpen }: { onOpen: () => void }) {
  const { status } = useUpdates();
  if (status?.phase !== 'ready') return null;
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-blue-300/20 bg-blue-400/10 px-8 py-2 text-xs text-blue-100" role="status">
      <span>ROM PolyBot v{status.latestVersion} is downloaded and verified.</span>
      <button className="font-semibold text-blue-200 underline underline-offset-2 hover:text-white" onClick={onOpen}>Review update</button>
    </div>
  );
}

function PageRouter({ page, setPage }: { page: PageId; setPage: (p: PageId) => void }) {
  switch (page) {
    case 'dashboard': return <OverviewPage onNav={setPage} />;
    case 'analytics': return <DashboardPage onNav={setPage} />;
    case 'evidence': return <EvidencePage onNav={setPage} />;
    case 'main': return <MainEnginePage onNav={setPage} />;
    case 'positions': return <PositionsPage />;
    case 'signals': return <SignalsPage />;
    case 'history': return <HistoryPage />;
    case 'profiles': return <ProfilesPage />;
    case 'settings': return <SettingsPage />;
    case 'api': return <ApiKeysPage />;
    case 'logs': return <LogsPage />;
    case 'guide': return <GuidePage />;
    case 'about': return <AboutPage />;
    case 'visualizer': return <VisualizerPage />;
    case 'terminal': return <TerminalPage />;
    case 'crypto15m': return <Crypto15mPage />;
    case 'accounts': return <AccountsPage />;
    case 'backtest': return <BacktestPage />;
    case 'scripts': return <ScriptsPage />;
    default: return <DashboardPage onNav={setPage} />;
  }
}
