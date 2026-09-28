import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

type UpdateStatus = Awaited<ReturnType<typeof window.rom.app.getUpdateStatus>>;

interface UpdateApi {
  status: UpdateStatus | null;
  busy: 'checking' | 'downloading' | 'installing' | null;
  actionError: string | null;
  check: () => Promise<void>;
  download: () => Promise<void>;
  cancelDownload: () => Promise<void>;
  install: () => Promise<void>;
}

const UpdateContext = createContext<UpdateApi | null>(null);
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;
const FIRST_CHECK_DELAY_MS = 10_000;

export function useUpdates(): UpdateApi {
  const value = useContext(UpdateContext);
  if (!value) throw new Error('useUpdates must be inside UpdateProvider');
  return value;
}

const errorMessage = (error: unknown): string => error instanceof Error
  ? error.message
  : 'Could not complete the update. Please try again.';

export function UpdateProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState<UpdateApi['busy']>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const statusRef = useRef<UpdateStatus | null>(null);
  const pendingRef = useRef(false);
  const skippedDownloadVersion = useRef<string | null>(null);

  const receive = useCallback((next: UpdateStatus) => {
    statusRef.current = next;
    setStatus(next);
  }, []);

  useEffect(() => {
    let active = true;
    const off = window.rom.app.onUpdateStatus((next) => {
      if (active) receive(next);
    });
    void window.rom.app.getUpdateStatus().then((next) => {
      if (active) receive(next);
    }).catch(() => {});
    return () => { active = false; off(); };
  }, [receive]);

  const run = useCallback(async (
    task: Exclude<UpdateApi['busy'], null>,
    action: () => Promise<unknown>,
  ) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setBusy(task);
    setActionError(null);
    try {
      await action();
      receive(await window.rom.app.getUpdateStatus());
    } catch (error) {
      // Main process owns the update state. Read it again in case the operation
      // failed after sending a more specific error or a recoverable phase.
      try {
        const latest = await window.rom.app.getUpdateStatus();
        if (task === 'installing') {
          // A refused install does not invalidate the verified download. Keep
          // the main-process status ready so the user can retry immediately.
          receive(latest);
          setActionError(errorMessage(error));
        } else {
          receive(latest.phase === 'error' || latest.phase === 'cancelled' ? latest : {
            ...latest,
            phase: 'error',
            message: errorMessage(error),
          });
        }
      } catch {
        if (task === 'installing') {
          setActionError(errorMessage(error));
        } else {
          receive({
            ...statusRef.current,
            currentVersion: statusRef.current?.currentVersion ?? '',
            phase: 'error',
            message: errorMessage(error),
          });
        }
      }
    } finally {
      pendingRef.current = false;
      setBusy(null);
    }
  }, [receive]);

  const check = useCallback(async () => {
    if (pendingRef.current || ['downloading', 'verifying', 'ready'].includes(statusRef.current?.phase ?? '')) return;
    await run('checking', () => window.rom.app.checkForUpdates());
  }, [run]);

  const download = useCallback(async () => {
    await run('downloading', () => window.rom.app.downloadUpdate());
  }, [run]);

  const cancelDownload = useCallback(async () => {
    try {
      // A cancelled version stays stopped until the user asks to download it.
      // The periodic release check must not restart the transfer behind them.
      skippedDownloadVersion.current = statusRef.current?.latestVersion ?? null;
      await window.rom.app.cancelUpdateDownload();
      receive(await window.rom.app.getUpdateStatus());
    } catch (error) {
      receive({
        ...statusRef.current,
        currentVersion: statusRef.current?.currentVersion ?? '',
        phase: 'error',
        message: errorMessage(error),
      });
    }
  }, [receive]);

  useEffect(() => {
    // Updates are discovered without delaying app startup or interrupting a
    // trading session. The install step always requires the user's click.
    const firstCheck = window.setTimeout(() => { void check(); }, FIRST_CHECK_DELAY_MS);
    const interval = window.setInterval(() => { void check(); }, CHECK_INTERVAL_MS);
    return () => {
      window.clearTimeout(firstCheck);
      window.clearInterval(interval);
    };
  }, [check]);

  useEffect(() => {
    if (status?.phase !== 'available' || !status.latestVersion || busy) return;
    if (skippedDownloadVersion.current === status.latestVersion) return;
    void download();
  }, [busy, download, status?.latestVersion, status?.phase]);

  const install = useCallback(async () => {
    await run('installing', async () => {
      const result = await window.rom.app.installUpdate();
      if (!result.ok) throw new Error(result.message);
    });
  }, [run]);

  const value = useMemo<UpdateApi>(() => ({
    status, busy, actionError, check, download, cancelDownload, install,
  }), [status, busy, actionError, check, download, cancelDownload, install]);

  return <UpdateContext.Provider value={value}>{children}</UpdateContext.Provider>;
}
