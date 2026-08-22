import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  Spinner,
  showModal,
  staticClasses,
} from '@decky/ui';
import { definePlugin, toaster } from '@decky/api';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { FaStore } from 'react-icons/fa';

import { BazaarState, InstalledPlugin, UpdateResult, checkUpdates, getState, untrack } from './lib/api';
import { AddRepoModal } from './components/AddRepoModal';
import { PluginDetailModal } from './components/PluginDetailModal';
import { SettingsModal } from './components/SettingsModal';

type UpdateMap = Record<string, UpdateResult>;

function statusFor(plugin: InstalledPlugin, update: UpdateResult | undefined): { badge: string; note: string } {
  if (!plugin.tracked) {
    return { badge: plugin.version || '—', note: 'No GitHub repo linked' };
  }
  const source = plugin.source!;
  const origin = `${source.owner}/${source.repo}`;
  if (!update) {
    return { badge: plugin.version || source.tag || '—', note: origin };
  }
  switch (update.status) {
    case 'update':
      return { badge: `→ ${update.latest_tag}`, note: `${origin} · update available` };
    case 'current':
      return { badge: plugin.version || update.installed_tag || '—', note: `${origin} · up to date` };
    case 'unknown':
      return { badge: plugin.version || '—', note: `${origin} · installed version unknown` };
    case 'error':
      return { badge: plugin.version || '—', note: `${origin} · check failed` };
  }
}

/** Updatable first, then linked, then unlinked, alphabetical within each group. */
function sortPlugins(plugins: InstalledPlugin[], updates: UpdateMap): InstalledPlugin[] {
  const rank = (p: InstalledPlugin) => {
    if (updates[p.name]?.status === 'update') return 0;
    if (p.tracked) return 1;
    return 2;
  };
  return [...plugins].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

function Content() {
  const [state, setState] = useState<BazaarState | null>(null);
  const [updates, setUpdates] = useState<UpdateMap>({});
  const [checking, setChecking] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setState(await getState());
    } catch (e) {
      toaster.toast({ title: 'Decky Bazaar', body: `Could not read installed plugins: ${e}` });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const runCheck = useCallback(async () => {
    setChecking(true);
    try {
      const res = await checkUpdates();
      setUpdates(res.results);
      const count = Object.values(res.results).filter((r) => r.status === 'update').length;
      const failed = Object.values(res.results).filter((r) => r.status === 'error').length;
      toaster.toast({
        title: 'Decky Bazaar',
        body:
          count > 0
            ? `${count} update${count === 1 ? '' : 's'} available.`
            : failed > 0
              ? `No updates found. ${failed} repo${failed === 1 ? '' : 's'} could not be checked.`
              : 'Everything is up to date.',
      });
    } catch (e) {
      toaster.toast({ title: 'Decky Bazaar', body: `Update check failed: ${e}` });
    } finally {
      setChecking(false);
      await refresh();
    }
  }, [refresh]);

  const plugins = useMemo(() => sortPlugins(state?.plugins ?? [], updates), [state, updates]);
  const trackedCount = plugins.filter((p) => p.tracked).length;
  const updateCount = plugins.filter((p) => updates[p.name]?.status === 'update').length;

  if (!state) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Spinner style={{ height: '32px' }} />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <>
      <PanelSection title="Bazaar">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => showModal(<AddRepoModal onDone={refresh} />)}
          >
            Add plugin from GitHub
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={checking || trackedCount === 0} onClick={runCheck}>
            {checking
              ? 'Checking…'
              : trackedCount === 0
                ? 'Nothing linked to check'
                : updateCount > 0
                  ? `Check again (${updateCount} available)`
                  : `Check ${trackedCount} plugin${trackedCount === 1 ? '' : 's'} for updates`}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() =>
              showModal(
                <SettingsModal settings={state.settings} hasToken={state.has_token} onDone={refresh} />,
              )
            }
          >
            Settings
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title={`Installed (${plugins.length})`}>
        {plugins.map((plugin) => {
          const update = updates[plugin.name];
          const { badge, note } = statusFor(plugin, update);
          return (
            <PanelSectionRow key={plugin.name}>
              <ButtonItem
                layout="inline"
                label={plugin.name}
                description={note}
                onClick={() =>
                  showModal(
                    <PluginDetailModal plugin={plugin} update={update} onRefresh={refresh} />,
                  )
                }
              >
                {badge}
              </ButtonItem>
            </PanelSectionRow>
          );
        })}
      </PanelSection>

      {state.orphans.length > 0 ? (
        <PanelSection title="Linked but not installed">
          {state.orphans.map((orphan) => (
            <PanelSectionRow key={orphan.name}>
              <ButtonItem
                layout="inline"
                label={orphan.name}
                description={`${orphan.source.owner}/${orphan.source.repo} · tap to forget`}
                onClick={async () => {
                  await untrack(orphan.name);
                  await refresh();
                }}
              >
                Forget
              </ButtonItem>
            </PanelSectionRow>
          ))}
        </PanelSection>
      ) : null}
    </>
  );
}

export default definePlugin(() => ({
  name: 'Decky Bazaar',
  titleView: <div className={staticClasses.Title}>Decky Bazaar</div>,
  content: <Content />,
  icon: <FaStore />,
}));
