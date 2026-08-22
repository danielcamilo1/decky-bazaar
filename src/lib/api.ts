import { callable } from '@decky/api';

/**
 * decky-loader exposes its own backend over a global websocket router. Reusing
 * `utilities/install_plugin` means installs go through the loader's real
 * pipeline (confirmation prompt, unzip, remote binaries, chown, hot reload)
 * instead of us reimplementing any of it.
 */
declare global {
  const DeckyBackend: {
    call<Args extends any[] = [], Return = void>(route: string, ...args: Args): Promise<Return>;
    callable<Args extends any[] = [], Return = void>(route: string): (...args: Args) => Promise<Return>;
    addEventListener(event: string, listener: (...args: any[]) => any): (...args: any[]) => any;
    removeEventListener(event: string, listener: (...args: any[]) => any): void;
  };
}

export enum InstallType {
  INSTALL = 0,
  REINSTALL = 1,
  UPDATE = 2,
  DOWNGRADE = 3,
  OVERWRITE = 4,
}

export const loaderInstallPlugin = (
  artifact: string,
  name: string,
  version: string,
  installType: InstallType = InstallType.INSTALL,
) => DeckyBackend.call<[string, string, string, string, InstallType]>(
  'utilities/install_plugin',
  artifact,
  name,
  version,
  '', // no hash: these zips are not store artifacts, so there is nothing to verify against
  installType,
);

export const loaderUninstallPlugin = (name: string) =>
  DeckyBackend.call<[string]>('utilities/uninstall_plugin', name);

/** Resolves when the loader reports the named plugin finished installing. */
export function onInstallFinished(name: string, timeoutMs = 180_000): Promise<boolean> {
  return new Promise((resolve) => {
    let timer: number | undefined;
    const listener = (finishedName: string) => {
      if (finishedName !== name) return;
      cleanup();
      resolve(true);
    };
    const cleanup = () => {
      if (timer !== undefined) clearTimeout(timer);
      DeckyBackend.removeEventListener('loader/plugin_download_finish', listener);
    };
    DeckyBackend.addEventListener('loader/plugin_download_finish', listener);
    timer = setTimeout(() => {
      cleanup();
      resolve(false);
    }, timeoutMs) as unknown as number;
  });
}

// ---------------------------------------------------------------- our backend

export interface SourceRecord {
  owner: string;
  repo: string;
  tag: string;
  asset_name: string;
  version: string;
  updated_at: number;
}

export interface InstalledPlugin {
  name: string;
  folder: string;
  author: string;
  description: string;
  version: string;
  api_version: number | null;
  source: SourceRecord | null;
  tracked: boolean;
  /** The on-disk version no longer matches what we recorded at install time. */
  drifted: boolean;
}

export interface BazaarSettings {
  include_prereleases: boolean;
}

export interface BazaarState {
  plugins: InstalledPlugin[];
  orphans: { name: string; source: SourceRecord }[];
  settings: BazaarSettings;
  has_token: boolean;
  self_name: string;
}

export interface ReleaseAsset {
  name: string;
  size: number;
  download_url: string;
  api_url: string;
}

export interface Release {
  tag: string;
  title: string;
  published_at: string;
  prerelease: boolean;
  notes: string;
  assets: ReleaseAsset[];
}

export type ReleaseListing = { ok: true; releases: Release[] } | { ok: false; error: string; status?: number };

export type StagedAsset =
  | {
      ok: true;
      artifact: string;
      size: number;
      plugin_name: string;
      plugin_version: string;
      root: string;
      author: string;
      description: string;
    }
  | { ok: false; error: string; status?: number };

export type UpdateStatus = 'current' | 'update' | 'unknown' | 'error';

export interface UpdateResult {
  status: UpdateStatus;
  error?: string;
  latest_tag?: string;
  latest_title?: string;
  published_at?: string;
  prerelease?: boolean;
  notes?: string;
  asset?: ReleaseAsset | null;
  installed_tag?: string;
}

export const getState = callable<[], BazaarState>('get_state');
export const getReleases = callable<
  [owner: string, repo: string, includePrereleases?: boolean | null],
  ReleaseListing
>('get_releases');
export const stageAsset = callable<[downloadUrl: string, apiUrl: string, assetName: string], StagedAsset>(
  'stage_asset',
);
export const track = callable<
  [pluginName: string, owner: string, repo: string, tag: string, assetName: string, version: string],
  { ok: boolean }
>('track');
export const untrack = callable<[pluginName: string], { ok: boolean }>('untrack');
export const checkUpdates = callable<
  [],
  { ok: boolean; results: Record<string, UpdateResult>; checked_at: number }
>('check_updates');
export const setSetting = callable<[key: string, value: any], { ok: boolean; settings?: BazaarSettings }>(
  'set_setting',
);
export const setToken = callable<[token: string], { ok: boolean; has_token: boolean }>('set_token');
