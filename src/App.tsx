import { lazy, Suspense, useEffect, useState, type ComponentType } from 'react';
import { StrategyActivityProvider } from './state/StrategyActivity';
import { TitleBar } from './components/TitleBar';
import { Sidebar } from './components/Sidebar';
import { WorkspaceStatus } from './components/WorkspaceStatus';
import { PageErrorBoundary } from './components/RendererErrorBoundary';
import { AppStateProvider, useApp } from './state/AppStateProvider';
import { ToastProvider } from './state/ToastProvider';
import { UpdateProvider, useUpdates } from './state/UpdateProvider';
import { OnboardingModal } from './pages/Onboarding';
import { OverviewPage } from './pages/Overview';

// Overview and onboarding are the first screens, so they ship in the main
// bundle. Every other page (and the charting library most of them use) loads
// on first visit, and all of them are fetched while the app is idle after
// startup, so switching pages stays instant without parsing them at launch.
const pageModules = {
  Dashboard: () => import('./pages/Dashboard'),
  Evidence: () => import('./pages/Evidence'),
  MainEngine: () => import('./pages/MainEngine'),
  Settings: () => import('./pages/Settings'),
  Positions: () => import('./pages/Positions'),
  Signals: () => import('./pages/Signals'),
  History: () => import('./pages/History'),
  Profiles: () => import('./pages/Profiles'),
  Logs: () => import('./pages/Logs'),
  ApiKeys: () => import('./pages/ApiKeys'),
  About: () => import('./pages/About'),
  Guide: () => import('./pages/Guide'),
  Visualizer: () => import('./pages/Visualizer'),
  Crypto15m: () => import('./pages/Crypto15m'),
  Accounts: () => import('./pages/Accounts'),
  Backtest: () => import('./pages/Backtest'),
  Terminal: () => import('./pages/Terminal'),
  Scripts: () => import('./pages/Scripts'),
};

function lazyPage<M, K extends keyof M>(load: () => Promise<M>, name: K) {
  return lazy(() => load().then((m) => ({ default: m[name] as ComponentType<any> })));
}

const DashboardPage = lazyPage(pageModules.Dashboard, 'DashboardPage');
const EvidencePage = lazyPage(pageModules.Evidence, 'EvidencePage');
const MainEnginePage = lazyPage(pageModules.MainEngine, 'MainEnginePage');
const SettingsPage = lazyPage(pageModules.Settings, 'SettingsPage');
const PositionsPage = lazyPage(pageModules.Positions, 'PositionsPage');
const SignalsPage = lazyPage(pageModules.Signals, 'SignalsPage');
const HistoryPage = lazyPage(pageModules.History, 'HistoryPage');
const ProfilesPage = lazyPage(pageModules.Profiles, 'ProfilesPage');
const LogsPage = lazyPage(pageModules.Logs, 'LogsPage');
const ApiKeysPage = lazyPage(pageModules.ApiKeys, 'ApiKeysPage');
const AboutPage = lazyPage(pageModules.About, 'AboutPage');
const GuidePage = lazyPage(pageModules.Guide, 'GuidePage');
const VisualizerPage = lazyPage(pageModules.Visualizer, 'VisualizerPage');
const Crypto15mPage = lazyPage(pageModules.Crypto15m, 'Crypto15mPage');
const AccountsPage = lazyPage(pageModules.Accounts, 'AccountsPage');
const BacktestPage = lazyPage(pageModules.Backtest, 'BacktestPage');
const TerminalPage = lazyPage(pageModules.Terminal, 'TerminalPage');
const ScriptsPage = lazyPage(pageModules.Scripts, 'ScriptsPage');

function prefetchPages() {
  for (const load of Object.values(pageModules)) {
    load().catch(() => { /* a page that fails here reports through the error boundary when opened */ });
  }
}

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

  useEffect(() => {
    const handle = window.requestIdleCallback(prefetchPages, { timeout: 4000 });
    return () => window.cancelIdleCallback(handle);
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
            <PageErrorBoundary key={page}>
              <Suspense fallback={<div className="h-full" aria-busy="true" />}>
                <PageRouter page={page} setPage={setPage} />
              </Suspense>
            </PageErrorBoundary>
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
