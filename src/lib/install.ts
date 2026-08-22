import { toaster } from '@decky/api';

import {
  InstallType,
  Release,
  ReleaseAsset,
  loaderInstallPlugin,
  onInstallFinished,
  stageAsset,
  track,
} from './api';
import { RepoRef } from './repo';

export interface InstallOutcome {
  ok: boolean;
  error?: string;
  pluginName?: string;
}

/**
 * Download a release zip, work out which plugin it actually contains, hand it to
 * decky-loader to install, and record where it came from once the loader says it
 * landed. `expectedName` guards updates so a repo cannot swap one plugin's
 * install for a different plugin.
 */
export async function installRelease(
  ref: RepoRef,
  release: Release,
  asset: ReleaseAsset,
  opts: { expectedName?: string; installType?: InstallType } = {},
): Promise<InstallOutcome> {
  const staged = await stageAsset(asset.download_url, asset.api_url, asset.name);
  if (!staged.ok) {
    return { ok: false, error: staged.error };
  }

  if (opts.expectedName && staged.plugin_name !== opts.expectedName) {
    return {
      ok: false,
      error: `That zip contains "${staged.plugin_name}", not "${opts.expectedName}". Nothing was installed.`,
    };
  }

  const pluginName = staged.plugin_name;
  const version = release.tag || staged.plugin_version || 'dev';
  const finished = onInstallFinished(pluginName);

  try {
    await loaderInstallPlugin(staged.artifact, pluginName, version, opts.installType ?? InstallType.INSTALL);
  } catch (e) {
    return { ok: false, error: `decky-loader refused the install: ${e}` };
  }

  const landed = await finished;
  if (!landed) {
    return {
      ok: false,
      pluginName,
      error: 'The install was never confirmed. If you dismissed the prompt, try again.',
    };
  }

  await track(pluginName, ref.owner, ref.repo, release.tag, asset.name, staged.plugin_version);
  return { ok: true, pluginName };
}

export function toastOutcome(outcome: InstallOutcome, verb: string) {
  if (outcome.ok) {
    toaster.toast({ title: 'Decky Bazaar', body: `${outcome.pluginName} ${verb}.` });
  } else {
    toaster.toast({ title: 'Decky Bazaar', body: outcome.error ?? 'Something went wrong.' });
  }
}
