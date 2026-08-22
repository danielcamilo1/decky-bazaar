import {
  ConfirmModal,
  DialogBody,
  DialogButton,
  DialogHeader,
  Field,
  ModalRoot,
  Spinner,
  showModal,
} from '@decky/ui';
import { toaster } from '@decky/api';
import { FC, useState } from 'react';

import { InstallType, InstalledPlugin, UpdateResult, loaderUninstallPlugin, untrack } from '../lib/api';
import { installRelease, toastOutcome } from '../lib/install';
import { repoUrl } from '../lib/repo';
import { AddRepoModal } from './AddRepoModal';

export interface PluginDetailModalProps {
  closeModal?(): void;
  plugin: InstalledPlugin;
  update?: UpdateResult;
  onRefresh(): void;
}

export const PluginDetailModal: FC<PluginDetailModalProps> = ({ closeModal, plugin, update, onRefresh }) => {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const source = plugin.source;

  const doUpdate = async () => {
    if (!source || !update?.asset || !update.latest_tag) return;
    setError('');
    setBusy(`Updating to ${update.latest_tag}…`);
    const outcome = await installRelease(
      { owner: source.owner, repo: source.repo },
      {
        tag: update.latest_tag,
        title: update.latest_title ?? update.latest_tag,
        published_at: update.published_at ?? '',
        prerelease: !!update.prerelease,
        notes: update.notes ?? '',
        assets: [update.asset],
      },
      update.asset,
      { expectedName: plugin.name, installType: InstallType.UPDATE },
    );
    setBusy('');
    toastOutcome(outcome, `updated to ${update.latest_tag}`);
    if (!outcome.ok) {
      setError(outcome.error ?? 'The update failed.');
      return;
    }
    onRefresh();
    closeModal?.();
  };

  const openSourceModal = () => {
    closeModal?.();
    showModal(
      <AddRepoModal
        onDone={onRefresh}
        existing={{
          name: plugin.name,
          owner: source?.owner,
          repo: source?.repo,
          installedVersion: plugin.version,
        }}
      />,
    );
  };

  const doUntrack = async () => {
    await untrack(plugin.name);
    onRefresh();
    closeModal?.();
  };

  const doUninstall = () => {
    closeModal?.();
    showModal(
      <ConfirmModal
        strTitle={`Uninstall ${plugin.name}?`}
        strDescription={`${plugin.name} will be removed from decky. Its settings on disk are cleaned up by decky-loader.`}
        strOKButtonText="Uninstall"
        bDestructiveWarning
        onOK={async () => {
          try {
            await loaderUninstallPlugin(plugin.name);
            await untrack(plugin.name);
            toaster.toast({ title: 'Decky Bazaar', body: `${plugin.name} uninstalled.` });
          } catch (e) {
            toaster.toast({ title: 'Decky Bazaar', body: `Could not uninstall ${plugin.name}: ${e}` });
          }
          onRefresh();
        }}
      />,
    );
  };

  const statusLine = (() => {
    if (!source) return 'Not linked to a GitHub repo yet.';
    if (!update) return `Tracking ${source.tag || 'an unknown release'}. Not checked yet.`;
    switch (update.status) {
      case 'update':
        return `Update available: ${update.latest_tag} (you have ${update.installed_tag || 'an unknown release'}).`;
      case 'current':
        return `Up to date on ${update.installed_tag}.`;
      case 'unknown':
        return `Latest release is ${update.latest_tag}, but which one you installed is unknown.`;
      case 'error':
        return `Update check failed: ${update.error}`;
    }
  })();

  return (
    <ModalRoot onCancel={closeModal} onEscKeypress={closeModal} bAllowFullSize>
      <DialogHeader>{plugin.name}</DialogHeader>
      <DialogBody>
        <Field label="Version" bottomSeparator="standard" description={plugin.description || undefined}>
          {plugin.version || 'unknown'}
        </Field>
        {plugin.author ? (
          <Field label="Author" bottomSeparator="standard">
            {plugin.author}
          </Field>
        ) : null}
        <Field label="Source" bottomSeparator="standard">
          {source ? repoUrl(source) : 'unlinked'}
        </Field>
        <Field label="Status" bottomSeparator="thick" description={statusLine} />

        {plugin.drifted ? (
          <div style={{ margin: '8px 0', fontSize: '12px', opacity: 0.75 }}>
            The installed version ({plugin.version}) differs from what Bazaar recorded (
            {source?.version || 'unknown'}). It was probably updated somewhere else.
          </div>
        ) : null}

        {error ? (
          <div style={{ margin: '10px 0', padding: '8px 10px', background: 'rgba(220, 80, 80, 0.15)', borderRadius: '4px', fontSize: '13px' }}>
            {error}
          </div>
        ) : null}

        {busy ? (
          <Field label={busy} bottomSeparator="none">
            <Spinner style={{ height: '28px' }} />
          </Field>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '12px' }}>
            {update?.status === 'update' && update.asset ? (
              <DialogButton onClick={doUpdate}>Update to {update.latest_tag}</DialogButton>
            ) : null}
            <DialogButton onClick={openSourceModal}>
              {source ? 'Change GitHub repo' : 'Link a GitHub repo'}
            </DialogButton>
            {source ? <DialogButton onClick={doUntrack}>Stop tracking</DialogButton> : null}
            <DialogButton onClick={doUninstall}>Uninstall</DialogButton>
          </div>
        )}
      </DialogBody>
    </ModalRoot>
  );
};
