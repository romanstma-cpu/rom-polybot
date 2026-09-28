import { app, BrowserWindow, shell } from 'electron';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, rename, rm, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { UpdateStatus } from '../../shared/types';
import { pythonBackend } from './python-backend';
import * as store from './settings-store';

const REPOSITORY = 'romanstma-cpu/rom-polybot';
const RELEASES_API = `https://api.github.com/repos/${REPOSITORY}/releases`;
const MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024;
const MAX_CHECKSUM_BYTES = 64 * 1024;
const VERSION = /^(?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8})$/;

export interface UpdateCheck {
  currentVersion: string;
  latestVersion: string;
  updateAvailable: boolean;
  releaseUrl: string;
  publishedAt: string | null;
}

interface GithubAsset { name?: unknown; size?: unknown }
interface GithubRelease {
  tag_name?: unknown;
  published_at?: unknown;
  draft?: unknown;
  prerelease?: unknown;
  assets?: GithubAsset[];
}

interface SelectedRelease {
  version: string;
  installerName: string;
  checksumName: string;
  installerSize: number | null;
  releaseUrl: string;
  publishedAt: string | null;
}

function expectedInstaller(version: string): string | null {
  if (process.platform === 'win32') return `ROM.PolyBot-Setup-${version}.exe`;
  if (process.platform === 'darwin' && process.arch === 'arm64') {
    return `ROM.PolyBot-${version}-arm64.dmg`;
  }
  return null;
}

function expectedChecksum(): string | null {
  if (process.platform === 'win32') return 'SHA256SUMS-windows-x64.txt';
  if (process.platform === 'darwin' && process.arch === 'arm64') {
    return 'SHA256SUMS-mac-apple-silicon.txt';
  }
  return null;
}

function compareVersions(a: string, b: string): number {
  const left = a.split('.').map(Number);
  const right = b.split('.').map(Number);
  for (let index = 0; index < 3; index++) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return 0;
}

function releaseAssetUrl(release: SelectedRelease, name: string): string {
  // The renderer never supplies a URL or file path. The version and asset names
  // were generated from a strict semver and our own platform-specific names.
  return `https://github.com/${REPOSITORY}/releases/download/v${release.version}/${name}`;
}

function assertGithubResponse(response: Response): void {
  if (!response.ok) throw new Error(`Update server returned ${response.status}`);
  const finalUrl = response.url;
  if (!finalUrl) return; // Mock Response objects used by the updater E2E tests.
  const url = new URL(finalUrl);
  if (url.protocol !== 'https:' || ![
    'github.com',
    'api.github.com',
    'release-assets.githubusercontent.com',
    'objects.githubusercontent.com',
  ].includes(url.hostname)) {
    throw new Error('Update server redirected to an untrusted host');
  }
}

function parseChecksum(contents: string, installerName: string): string {
  let found: string | null = null;
  for (const line of contents.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const match = /^([a-f\d]{64})  ([^\r\n]+)$/i.exec(line);
    if (!match) throw new Error('Release checksum file has an invalid format');
    if (match[2] !== installerName) continue;
    if (found) throw new Error('Release checksum file lists the installer more than once');
    found = match[1].toLowerCase();
  }
  if (!found) throw new Error('Release checksum file does not include this installer');
  return found;
}

async function checksumOfFile(path: string): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}

async function limitedText(response: Response): Promise<string> {
  if (!response.body) throw new Error('Update server returned an empty checksum');
  const length = Number(response.headers?.get('content-length') ?? NaN);
  if (Number.isFinite(length) && length > MAX_CHECKSUM_BYTES) {
    throw new Error('Release checksum file is too large');
  }
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of Readable.fromWeb(response.body as never)) {
    const bytes = Buffer.from(chunk);
    size += bytes.length;
    if (size > MAX_CHECKSUM_BYTES) throw new Error('Release checksum file is too large');
    chunks.push(bytes);
  }
  return Buffer.concat(chunks).toString('utf8');
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return 'The update could not be completed.';
}

export class UpdateService {
  private status: UpdateStatus = { phase: 'idle', currentVersion: app.getVersion() };
  private selected: SelectedRelease | null = null;
  private readyPath: string | null = null;
  private readyHash: string | null = null;
  private checkTask: Promise<UpdateCheck> | null = null;
  private downloadTask: Promise<{ version: string; ready: true }> | null = null;
  private downloadController: AbortController | null = null;
  private installing = false;
  private installerOpened = false;

  getStatus(): UpdateStatus { return { ...this.status }; }

  private emit(next: UpdateStatus): void {
    this.status = next;
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed()) window.webContents.send('app:updateStatus', this.getStatus());
    }
  }

  private setPhase(phase: UpdateStatus['phase'], values: Partial<UpdateStatus> = {}): void {
    this.emit({ ...this.status, phase, ...values });
  }

  async check(): Promise<UpdateCheck> {
    if (this.checkTask) return this.checkTask;
    if (this.downloadTask || this.status.phase === 'ready') {
      if (!this.selected) throw new Error('No update has been selected');
      return this.toCheck(this.selected);
    }
    const task = this.performCheck();
    this.checkTask = task;
    try { return await task; } finally { this.checkTask = null; }
  }

  private toCheck(release: SelectedRelease): UpdateCheck {
    return {
      currentVersion: app.getVersion(),
      latestVersion: release.version,
      updateAvailable: compareVersions(release.version, app.getVersion()) > 0,
      releaseUrl: release.releaseUrl,
      publishedAt: release.publishedAt,
    };
  }

  private async performCheck(): Promise<UpdateCheck> {
    const currentVersion = app.getVersion();
    const checksumName = expectedChecksum();
    if (!checksumName) {
      const message = 'In-app updates support Windows and Apple Silicon macOS builds.';
      this.setPhase('error', { message });
      throw new Error(message);
    }
    this.setPhase('checking', { message: 'Checking for the latest ROM PolyBot release…' });
    try {
      let selected: SelectedRelease | null = null;
      for (let page = 1; page <= 3; page++) {
        const response = await fetch(`${RELEASES_API}?per_page=100&page=${page}`, {
          headers: { Accept: 'application/vnd.github+json', 'User-Agent': `ROM-PolyBot/${currentVersion}` },
          signal: AbortSignal.timeout(10_000),
        });
        assertGithubResponse(response);
        const batch = await response.json() as unknown;
        if (!Array.isArray(batch)) throw new Error('Update server returned an invalid release list');
        for (const release of batch as GithubRelease[]) {
          if (!release || release.draft || release.prerelease || !Array.isArray(release.assets)) continue;
          const tag = typeof release.tag_name === 'string' ? release.tag_name : '';
          const version = tag.startsWith('v') ? tag.slice(1) : '';
          if (!VERSION.test(version)) continue;
          const installerName = expectedInstaller(version);
          if (!installerName) continue;
          const installer = release.assets.find((asset) => asset?.name === installerName);
          const hasChecksum = release.assets.some((asset) => asset?.name === checksumName);
          if (!installer || !hasChecksum) continue;
          const rawSize = installer.size;
          const size = typeof rawSize === 'number' && Number.isSafeInteger(rawSize) && rawSize > 0
            ? rawSize : null;
          if (size !== null && size > MAX_INSTALLER_BYTES) continue;
          if (!selected || compareVersions(version, selected.version) > 0) {
            selected = {
              version, installerName, checksumName, installerSize: size,
              releaseUrl: `https://github.com/${REPOSITORY}/releases/tag/v${version}`,
              publishedAt: typeof release.published_at === 'string' ? release.published_at : null,
            };
          }
        }
        if (batch.length < 100) break;
      }
      if (!selected) throw new Error('No published ROM PolyBot installer with a checksum was found');
      this.selected = selected;
      this.readyPath = null;
      this.readyHash = null;
      const result = this.toCheck(selected);
      this.emit({
        phase: result.updateAvailable ? 'available' : 'up-to-date',
        currentVersion,
        latestVersion: selected.version,
        releaseUrl: selected.releaseUrl,
        publishedAt: selected.publishedAt,
        message: result.updateAvailable
          ? `ROM PolyBot ${selected.version} is ready to download.`
          : 'ROM PolyBot is up to date.',
      });
      return result;
    } catch (error) {
      this.setPhase('error', { message: errorMessage(error) });
      throw error;
    }
  }

  async download(): Promise<{ version: string; ready: true }> {
    if (this.downloadTask) return this.downloadTask;
    if (this.status.phase === 'ready' && this.selected && this.readyPath) {
      return { version: this.selected.version, ready: true };
    }
    const task = this.performDownload();
    this.downloadTask = task;
    try { return await task; } finally { this.downloadTask = null; }
  }

  private async performDownload(): Promise<{ version: string; ready: true }> {
    if (!this.selected) await this.check();
    const release = this.selected;
    if (!release || compareVersions(release.version, app.getVersion()) <= 0) {
      throw new Error('ROM PolyBot is already up to date.');
    }
    const controller = new AbortController();
    this.downloadController = controller;
    const timeout = setTimeout(() => controller.abort(new Error('Update download timed out')), 30 * 60_000);
    const directory = join(app.getPath('userData'), 'updates', release.version);
    const target = join(directory, release.installerName);
    const partial = `${target}.partial`;
    try {
      this.setPhase('downloading', {
        currentVersion: app.getVersion(), latestVersion: release.version,
        releaseUrl: release.releaseUrl, publishedAt: release.publishedAt,
        receivedBytes: 0, totalBytes: release.installerSize,
        message: `Downloading ROM PolyBot ${release.version}…`,
      });
      const checksumResponse = await fetch(releaseAssetUrl(release, release.checksumName), {
        headers: { 'User-Agent': `ROM-PolyBot/${app.getVersion()}` },
        signal: controller.signal,
      });
      assertGithubResponse(checksumResponse);
      const expectedHash = parseChecksum(await limitedText(checksumResponse), release.installerName);
      controller.signal.throwIfAborted();
      await mkdir(directory, { recursive: true, mode: 0o700 });

      try {
        const existing = await stat(target);
        if (existing.isFile() && existing.size <= MAX_INSTALLER_BYTES
          && await checksumOfFile(target) === expectedHash) {
          controller.signal.throwIfAborted();
          this.readyPath = target;
          this.readyHash = expectedHash;
          this.setPhase('ready', {
            receivedBytes: existing.size, totalBytes: existing.size,
            message: `ROM PolyBot ${release.version} is verified and ready to install.`,
          });
          return { version: release.version, ready: true };
        }
      } catch (error) {
        if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
      }
      await rm(target, { force: true });
      await rm(partial, { force: true });

      const response = await fetch(releaseAssetUrl(release, release.installerName), {
        headers: { 'User-Agent': `ROM-PolyBot/${app.getVersion()}` },
        signal: controller.signal,
      });
      assertGithubResponse(response);
      if (!response.body) throw new Error('Update server returned an empty installer');
      const rawLength = Number(response.headers?.get('content-length') ?? NaN);
      const length = Number.isSafeInteger(rawLength) && rawLength >= 0 ? rawLength : null;
      if (length !== null && length > MAX_INSTALLER_BYTES) throw new Error('Installer exceeds the download limit');
      if (release.installerSize !== null && length !== null && length !== release.installerSize) {
        throw new Error('Installer size does not match the release listing');
      }
      let received = 0;
      let lastProgress = 0;
      const hash = createHash('sha256');
      await pipeline(
        Readable.fromWeb(response.body as never),
        new Transform({
          transform: (chunk: Buffer, _encoding, callback) => {
            received += chunk.length;
            if (received > MAX_INSTALLER_BYTES ||
              (release.installerSize !== null && received > release.installerSize)) {
              callback(new Error('Installer exceeds its declared size'));
              return;
            }
            hash.update(chunk);
            if (Date.now() - lastProgress > 200) {
              lastProgress = Date.now();
              this.setPhase('downloading', {
                receivedBytes: received, totalBytes: length ?? release.installerSize,
              });
            }
            callback(null, chunk);
          },
        }),
        createWriteStream(partial, { flags: 'wx', mode: 0o600 }),
        { signal: controller.signal },
      );
      controller.signal.throwIfAborted();
      if (received === 0) throw new Error('Update server returned an empty installer');
      if (length !== null && received !== length) throw new Error('Installer download was incomplete');
      if (release.installerSize !== null && received !== release.installerSize) {
        throw new Error('Installer size does not match the release listing');
      }
      this.setPhase('verifying', {
        receivedBytes: received, totalBytes: received, message: 'Verifying the installer checksum…',
      });
      if (hash.digest('hex') !== expectedHash) throw new Error('Installer checksum does not match the published release');
      controller.signal.throwIfAborted();
      await rename(partial, target);
      this.readyPath = target;
      this.readyHash = expectedHash;
      this.setPhase('ready', {
        receivedBytes: received, totalBytes: received,
        message: `ROM PolyBot ${release.version} is verified and ready to install.`,
      });
      return { version: release.version, ready: true };
    } catch (error) {
      await rm(partial, { force: true }).catch(() => {});
      this.readyPath = null;
      this.readyHash = null;
      if (controller.signal.reason === 'cancelled') {
        this.setPhase('cancelled', { message: 'Update download cancelled.' });
      } else {
        this.setPhase('error', { message: errorMessage(controller.signal.reason ?? error) });
      }
      throw error;
    } finally {
      clearTimeout(timeout);
      this.downloadController = null;
    }
  }

  async cancelDownload(): Promise<{ cancelled: boolean }> {
    if (!this.downloadTask || !this.downloadController) return { cancelled: false };
    this.downloadController.abort('cancelled');
    await this.downloadTask.catch(() => {});
    return { cancelled: true };
  }

  async install(): Promise<{ ok: boolean; message: string }> {
    if (this.installerOpened) {
      return { ok: true, message: 'The verified installer is already opening.' };
    }
    if (this.installing) {
      return { ok: false, message: 'The update is already being prepared for installation.' };
    }
    this.installing = true;
    try { return await this.performInstall(); } finally { this.installing = false; }
  }

  private async performInstall(): Promise<{ ok: boolean; message: string }> {
    const release = this.selected;
    const path = this.readyPath;
    const expectedHash = this.readyHash;
    if (!release || !path || !expectedHash || this.status.phase !== 'ready') {
      return { ok: false, message: 'Download and verify the update before installing it.' };
    }
    if (!app.isPackaged) {
      return { ok: false, message: 'Installer launch is available only in the packaged app.' };
    }
    try {
      const info = await stat(path);
      if (!info.isFile() || info.size > MAX_INSTALLER_BYTES ||
        await checksumOfFile(path) !== expectedHash) {
        throw new Error('Downloaded installer failed its final checksum check. Download it again.');
      }
      const config = store.get().config;
      if (config.enableTrading || config.crypto15mEnabled || config.scriptsLiveEnabled) {
        return {
          ok: false,
          message: 'Pause all live trading strategies before installing. Open orders remain your responsibility during the restart.',
        };
      }
      if (process.platform === 'darwin') {
        const openError = await shell.openPath(path);
        if (openError) throw new Error(`Could not open the update disk image: ${openError}`);
        this.installerOpened = true;
        return {
          ok: true,
          message: 'Verified update opened. Drag ROM PolyBot into Applications, then reopen the app.',
        };
      }
      if (process.platform !== 'win32') {
        return { ok: false, message: 'This platform does not support in-app installation.' };
      }
      // Stop the bundled backend before starting NSIS. The installer opens
      // before Electron quits, but cannot reach its replacement step until
      // after the user advances its wizard; the app quits immediately after
      // the IPC response. No renderer-controlled path is accepted.
      await pythonBackend.stop();
      const stopDeadline = Date.now() + 8_000;
      while (pythonBackend.info().pid !== null && Date.now() < stopDeadline) {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      if (pythonBackend.info().pid !== null) {
        throw new Error('Trading backend did not stop. Quit the app normally and retry the update.');
      }
      const child = spawn(path, [], {
        detached: true, stdio: 'ignore', windowsHide: false,
      });
      await new Promise<void>((resolve, reject) => {
        child.once('spawn', () => resolve());
        child.once('error', reject);
      });
      child.unref();
      this.installerOpened = true;
      // Let the IPC reply reach the renderer before Electron quits. The
      // before-quit handler sets the tray/window close flag and stops Python.
      setTimeout(() => app.quit(), 300);
      return { ok: true, message: 'Verified installer opened. ROM PolyBot is closing so it can be replaced.' };
    } catch (error) {
      this.setPhase('error', { message: errorMessage(error) });
      return { ok: false, message: errorMessage(error) };
    }
  }
}

export const updateService = new UpdateService();
