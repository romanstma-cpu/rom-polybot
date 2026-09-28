import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Download, RefreshCw, RotateCcw } from 'lucide-react';
import type { TraderConfig } from '@shared/types';
import { useApp } from '../state/AppStateProvider';
import { useToast } from '../state/ToastProvider';
import { useUpdates } from '../state/UpdateProvider';
import { Card, Page, Section, Switch } from '../components/common';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function useFocusTrap(open: boolean, containerRef: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    if (!open || !containerRef.current) return;

    const el = containerRef.current;
    const prevFocus = document.activeElement as HTMLElement | null;

    // Focus first focusable element in the dialog on open
    const first = el.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();

    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        // Let the caller handle close via onClose
        return;
      }
      if (e.key !== 'Tab') return;

      const nodes = el.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handler);

    // Disable scroll behind the dialog
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', handler);
      document.body.style.overflow = '';
      prevFocus?.focus();
    };
  }, [open, containerRef]);
}


export function SettingsPage() {
  const { config, refresh, state, backend } = useApp();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const restartBackend = async (): Promise<void> => {
    toast.info('Restarting backend — every engine stops and restarts…');
    await window.rom.backend.restart();
    setTimeout(() => void refresh.backend(), 1000);
  };

  if (!config) return <Page title="Settings"><div className="text-rom-muted">Loading…</div></Page>;

  const update = async <K extends keyof TraderConfig>(key: K, value: TraderConfig[K]): Promise<void> => {
    try {
      await window.rom.config.update({ [key]: value } as Partial<TraderConfig>);
      await refresh.state();
    } catch (e: any) {
      toast.error(`${e?.message || e}`);
    }
  };

  return (
    <Page
      title="Settings"
      subtitle="App-level preferences for startup behavior, notifications, and data. Main Strategy, Crypto, and Scripts each have their own controls."
    >
      <UpdateControls liveEngineCount={[config.enableTrading, config.crypto15mEnabled, config.scriptsLiveEnabled].filter(Boolean).length} />

      <Section
        title="Backend"
        description="The Python process every engine runs inside. Restarting it stops and restarts ALL of them — it is not scoped to any single engine, which is why it lives here rather than on an engine page."
      >
        <Card>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <button onClick={() => void restartBackend()} className="rom-btn-default">
              <RefreshCw className="h-4 w-4" /> Restart backend
            </button>
            <div className="text-[11px] text-rom-dim">
              Status:{' '}
              <span className={backend.status === 'running' ? 'text-rom-win' : 'text-rom-warn'}>
                {backend.status}
              </span>
              {backend.status === 'running'
                ? ' — open positions keep their management passes; a restart briefly interrupts them.'
                : ' — a restart usually clears a stuck backend.'}
            </div>
          </div>
        </Card>
      </Section>

      <Section title="App preferences">
        <Card>
          <div className="grid gap-2 md:grid-cols-2">
            <Switch
              label="Start at login"
              description="Launch ROM PolyBot when you sign in (silent if Start Minimized is on)."
              checked={!!state?.startWithWindows}
              onChange={(v) => window.rom.state.setStartWithWindows(v).then(refresh.state)}
            />
            <Switch
              label="Start minimized to tray"
              description="If autostarted, hide to tray on launch. Open from the tray icon."
              checked={!!state?.startMinimized}
              onChange={(v) => window.rom.state.setStartMinimized(v).then(refresh.state)}
            />
          </div>
        </Card>
      </Section>

      <Section
        title="Discord webhooks (optional)"
        description="Drop your channel webhook URLs to mirror events to Discord."
      >
        <Card>
          <div className="grid gap-3 md:grid-cols-2">
            <UrlField label="Trade events" value={config.eventWebhookUrl}
              onChange={(v) => void update('eventWebhookUrl', v)} />
            <UrlField label="Stats" value={config.statsWebhookUrl}
              onChange={(v) => void update('statsWebhookUrl', v)} />
            <UrlField label="Large Trade alerts" value={config.whaleWebhookUrl}
              onChange={(v) => void update('whaleWebhookUrl', v)} />
            <UrlField label="Momentum alerts" value={config.momentumWebhookUrl}
              onChange={(v) => void update('momentumWebhookUrl', v)} />
            <UrlField label="Safety alerts" value={config.alertWebhookUrl}
              onChange={(v) => void update('alertWebhookUrl', v)} />
          </div>
          <p className="mt-2 text-xs text-rom-muted">
            Safety alerts fire when trading pauses or needs you — drawdown or
            daily stop, execution circuit open, an order awaiting recovery, auth
            lost, the kill switch, or a deposit/withdrawal. Blank falls back to
            the Trade events channel.
          </p>
          <Switch
            label="Enable Discord webhooks"
            description="Master switch — turn off to mute all webhook posting without losing the URLs."
            checked={config.enableDiscord}
            onChange={(v) => void update('enableDiscord', v)}
          />
        </Card>
      </Section>

      <DangerZone busy={busy} setBusy={setBusy} />
    </Page>
  );
}

function UpdateControls({ liveEngineCount }: { liveEngineCount: number }) {
  const { status, busy, actionError, check, download, cancelDownload, install } = useUpdates();
  const [installedVersion, setInstalledVersion] = useState('');
  const [confirmInstall, setConfirmInstall] = useState(false);
  const installDialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(confirmInstall, installDialogRef);

  useEffect(() => {
    void window.rom.app.version().then(setInstalledVersion).catch(() => setInstalledVersion('Unavailable'));
  }, []);

  const isMac = /Mac/i.test(navigator.platform);
  const phase = status?.phase ?? 'idle';
  const transferring = phase === 'downloading' || phase === 'verifying';
  const checkDisabled = !!busy || transferring || phase === 'ready';
  const percent = status?.totalBytes && status.totalBytes > 0
    ? Math.min(100, Math.round(((status.receivedBytes ?? 0) / status.totalBytes) * 100))
    : null;
  const version = status?.latestVersion ? `v${status.latestVersion}` : 'the latest version';
  const title = phase === 'ready' ? `${version} is ready to install`
    : phase === 'downloading' ? `Downloading ${version}`
    : phase === 'verifying' ? 'Verifying download'
    : phase === 'available' ? `${version} is available`
    : phase === 'up-to-date' ? 'You are up to date'
    : phase === 'checking' || busy === 'checking' ? 'Checking for updates…'
    : phase === 'cancelled' ? 'Download stopped'
    : phase === 'error' ? 'Update needs attention'
    : 'Updates are checked automatically';
  const detail = phase === 'ready'
    ? isMac
      ? 'The Apple Silicon download passed its integrity check. Open the disk image, quit PolyBot, and replace it in Applications.'
      : 'The installer passed its integrity check. You can install it here when you are ready to stop trading.'
    : phase === 'downloading'
      ? percent === null ? 'Downloading in the background…' : `${percent}% downloaded`
    : phase === 'verifying' ? 'Checking the downloaded file before it can be opened.'
    : phase === 'available' ? 'Starting the background download…'
    : phase === 'up-to-date' ? `Latest public release: ${version}.`
    : phase === 'cancelled' ? 'You can start the download again now. PolyBot will also check for updates on its next launch.'
    : phase === 'error' ? (status?.message || 'Could not complete the update. Try again.')
    : phase === 'checking' ? 'Looking for the latest public release.'
    : 'PolyBot checks at startup and every six hours. Downloads happen in the background; installation requires your approval.';

  return (
    <Section title="Version and updates" description="Keep PolyBot current without visiting the download site.">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-rom-dim">Installed version</p>
            <p className="mt-1 font-mono text-2xl font-semibold text-white">{installedVersion ? `v${installedVersion}` : 'Checking…'}</p>
          </div>
          <button className="rom-btn-default" disabled={checkDisabled} onClick={() => void check()}>
            <RefreshCw className={`h-4 w-4 ${phase === 'checking' || busy === 'checking' ? 'animate-spin' : ''}`} />
            {phase === 'checking' || busy === 'checking' ? 'Checking…' : 'Check now'}
          </button>
        </div>

        <div className={`mt-4 rounded-xl border p-4 ${phase === 'error' ? 'border-rom-loss/35 bg-rom-loss/5' : phase === 'up-to-date' ? 'border-rom-win/30 bg-rom-win/5' : 'border-blue-300/25 bg-blue-400/5'}`} aria-live="polite">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              {phase === 'error' ? <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rom-lossText" />
                : phase === 'up-to-date' ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-rom-win" />
                : <Download className="mt-0.5 h-5 w-5 shrink-0 text-blue-300" />}
              <div>
                <p className="text-sm font-semibold text-white">{title}</p>
                <p className={`mt-1 text-xs leading-5 ${phase === 'error' ? 'text-rom-lossText' : 'text-rom-dim'}`}>{detail}</p>
              </div>
            </div>
            {phase === 'ready' && <button className="rom-btn-primary" onClick={() => setConfirmInstall(true)}>{liveEngineCount > 0 ? 'Review update' : isMac ? 'Open installer' : 'Install and restart'}</button>}
            {(phase === 'cancelled' || phase === 'available') && <button className="rom-btn-default" disabled={!!busy} onClick={() => void download()}>Download now</button>}
            {phase === 'error' && <button className="rom-btn-default" disabled={!!busy} onClick={() => void check()}>Try again</button>}
            {phase === 'downloading' && <button className="rom-btn-default" onClick={() => void cancelDownload()}>Stop download</button>}
          </div>
          {phase === 'downloading' && (
            <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/10" role="progressbar" aria-label="Update download" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}>
              <div className="h-full rounded-full bg-blue-400 transition-all duration-300" style={{ width: percent === null ? '15%' : `${percent}%` }} />
            </div>
          )}
        </div>
        {actionError && phase === 'ready' && <p role="alert" className="mt-3 rounded-lg border border-rom-loss/30 bg-rom-loss/5 px-3 py-2 text-xs leading-5 text-rom-lossText">Could not start installation: {actionError} The verified update is still ready; review it and try again.</p>}
        <p className="mt-4 text-xs leading-5 text-rom-dim">This version is the executable currently running. Installing an update never happens automatically while PolyBot is trading.</p>
      </Card>

      {confirmInstall && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label="Confirm PolyBot update" onClick={() => setConfirmInstall(false)} onKeyDown={(event) => { if (event.key === 'Escape') setConfirmInstall(false); }}>
          <div ref={installDialogRef} className="w-full max-w-md rounded-2xl border border-blue-300/30 bg-rom-panel p-6 shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <h3 className="text-lg font-semibold text-white">{isMac ? 'Open the verified update?' : 'Install the verified update?'}</h3>
            <p className="mt-3 text-sm leading-6 text-rom-muted">
              {isMac
                ? 'PolyBot will open the downloaded disk image. Quit this copy, drag the new app to Applications, then reopen it to finish updating.'
                : 'PolyBot will close and start the installer. Running strategies will stop until you reopen the app.'}
            </p>
            {liveEngineCount > 0 && <p className="mt-3 rounded-lg border border-rom-warn/30 bg-rom-warn/10 p-3 text-xs leading-5 text-rom-warn">{liveEngineCount} live engine{liveEngineCount === 1 ? ' is' : 's are'} enabled. Turn off live trading in Strategy, Crypto, and Scripts before installing. Existing positions stay open, but PolyBot cannot manage them while it is closed.</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button className="rom-btn-default" onClick={() => setConfirmInstall(false)}>Later</button>
              <button className="rom-btn-primary" disabled={busy === 'installing' || liveEngineCount > 0} onClick={() => { setConfirmInstall(false); void install(); }}>{busy === 'installing' ? 'Opening…' : isMac ? 'Open disk image' : 'Close and install'}</button>
            </div>
          </div>
        </div>
      )}
    </Section>
  );
}

function DangerZone({
  busy, setBusy,
}: { busy: boolean; setBusy: (b: boolean) => void }) {
  const toast = useToast();

  const [showModal, setShowModal] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);
  useFocusTrap(showModal, modalRef);
  const [phrase, setPhrase] = useState('');

  const openModal = (): void => {
    setPhrase('');
    setShowModal(true);
  };

  const cancel = (): void => {
    setShowModal(false);
    setPhrase('');
  };

  const confirm = async (): Promise<void> => {
    if (phrase.trim() !== 'RESET') {
      toast.error('Type RESET (uppercase) to confirm.');
      return;
    }
    setShowModal(false);
    setBusy(true);
    try {
      const r = await window.rom.app.factoryReset();
      if (r.ok) {
        const summary = (r.data as { deleted?: Record<string, number> })?.deleted || {};
        const detail = Object.entries(summary)
          .filter(([k, v]) => !k.startsWith('_') && (v as number) > 0)
          .map(([k, v]) => `${k}: ${v}`)
          .join(', ');
        toast.success(detail
          ? `Wiped — ${detail}`
          : (r.message || 'Local data cleared (nothing to delete)'));
      } else {
        toast.error(r.message || 'Reset failed');
      }
    } catch (e: any) {
      toast.error(`${e?.message || e}`);
    } finally {
      setBusy(false);
      setPhrase('');
    }
  };

  return (
    <>
      <Section title="Danger zone">
        <Card>
          <div className="space-y-3 rounded-xl border border-rose-500/40 bg-rose-500/5 p-4">
            <div>
              <div className="text-sm font-semibold text-rose-300">
                Full reset
              </div>
              <p className="text-xs text-rom-muted mt-1">
                Wipes all locally stored trading history, P&amp;L snapshots,
                bot runs, and signals. Your API credentials, profiles, and settings are
                preserved. Live Polymarket positions will be re-imported on the
                next reconcile cycle. Useful for clearing inconsistent state
                from old builds before live testing.
              </p>
            </div>
            <button
              onClick={openModal}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-rose-500/60 bg-rose-500/15 px-4 py-2 text-sm font-semibold text-rose-200 hover:bg-rose-500/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw className="h-4 w-4" />
              Full reset
            </button>
          </div>
        </Card>
      </Section>

      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label="Confirm full reset"
          onClick={cancel}
        >
          <div
            ref={modalRef}
            className="w-full max-w-md rounded-2xl border border-rose-500/50 bg-rom-panel p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-rose-300">
              Confirm full reset
            </h3>
            <div className="mt-3 space-y-2 text-sm text-rom-muted">
              <p>This will permanently delete:</p>
              <ul className="ml-5 list-disc space-y-1">
                <li>all bot positions and trade history</li>
                <li>all bot runs (session P&amp;L)</li>
                <li>all P&amp;L snapshots</li>
                <li>all Large Trade and Momentum signals</li>
              </ul>
              <p className="pt-2">
                Your API credentials, profiles, and settings are <strong>kept</strong>.
                Live Polymarket positions will be re-imported on the next
                reconcile cycle.
              </p>
            </div>
            <label className="mt-4 block text-xs font-semibold uppercase tracking-wider text-rom-muted">
              Type <span className="text-rose-300">RESET</span> to confirm
            </label>
            <input
              autoFocus
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void confirm();
                if (e.key === 'Escape') cancel();
              }}
              className="mt-1 w-full rounded-lg border border-rose-500/40 bg-black/30 px-3 py-2 text-sm font-mono text-rose-100 placeholder-rom-muted/50 outline-none focus:border-rose-400"
              placeholder="RESET"
            />
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={cancel}
                className="rounded-lg border border-white/10 bg-white/5 px-4 py-2 text-sm font-semibold text-rom-muted hover:bg-white/10"
              >
                Cancel
              </button>
              <button
                onClick={() => void confirm()}
                disabled={phrase.trim() !== 'RESET'}
                className="rounded-lg border border-rose-500/60 bg-rose-500/20 px-4 py-2 text-sm font-semibold text-rose-100 hover:bg-rose-500/30 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Wipe everything
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function UrlField({
  label, value, onChange,
}: { label: string; value: string; onChange: (v: string) => void }) {
  const [text, setText] = useState(value);
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setText(value); }, [value]);

  return (
    <div>
      <label className="rom-label">{label} webhook</label>
      <input
        type="text"
        className="rom-input font-mono text-xs"
        value={text}
        placeholder="https://discord.com/api/webhooks/…"
        onFocus={() => { focused.current = true; }}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => { focused.current = false; if (text !== value) onChange(text); }}
      />
    </div>
  );
}
