import {
  DialogBody,
  DialogButton,
  DialogHeader,
  Dropdown,
  Field,
  ModalRoot,
  Spinner,
} from '@decky/ui';
import { FC, useMemo, useState } from 'react';

import {
  InstallType,
  Release,
  ReleaseAsset,
  getBranches,
  getReleases,
  releaseMatchesBranch,
  track,
} from '../lib/api';
import { installRelease, toastOutcome } from '../lib/install';
import { RepoRef, isValidOwner, isValidRepo } from '../lib/repo';
import { RepoInput } from './RepoInput';

export interface AddRepoModalProps {
  closeModal?(): void;
  onDone(): void;
  /** Set when re-pointing a plugin that is already installed. */
  existing?: {
    name: string;
    owner?: string;
    repo?: string;
    branch?: string;
    installedVersion?: string;
  };
}

type Step = 'repo' | 'pick' | 'busy';

/** The dropdown value for "do not pin to a branch at all". */
const ANY_BRANCH = '';

function formatSize(bytes: number): string {
  if (!bytes) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

function formatDate(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '' : d.toLocaleDateString();
}

export const AddRepoModal: FC<AddRepoModalProps> = ({ closeModal, onDone, existing }) => {
  const [owner, setOwner] = useState(existing?.owner ?? '');
  const [repo, setRepo] = useState(existing?.repo ?? '');
  const [step, setStep] = useState<Step>('repo');
  const [busyLabel, setBusyLabel] = useState('');
  const [error, setError] = useState('');
  const [releases, setReleases] = useState<Release[]>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [defaultBranch, setDefaultBranch] = useState('');
  const [branchError, setBranchError] = useState('');
  const [branch, setBranch] = useState(existing?.branch ?? ANY_BRANCH);
  const [releaseIndex, setReleaseIndex] = useState(0);
  const [assetIndex, setAssetIndex] = useState(0);

  /** Which `owner/repo` the branches on screen belong to. */
  const [loadedFor, setLoadedFor] = useState(
    existing?.owner && existing?.repo ? `${existing.owner}/${existing.repo}` : '',
  );

  const ref: RepoRef = { owner: owner.trim(), repo: repo.trim() };
  const canSearch = isValidOwner(ref.owner) && isValidRepo(ref.repo);

  // Filtering here rather than re-asking the backend keeps switching branches
  // free, which matters against a 60-requests-per-hour rate limit.
  const visible = useMemo(
    () => releases.filter((r) => releaseMatchesBranch(r, branch)),
    [releases, branch],
  );
  const release: Release | undefined = visible[releaseIndex];
  const asset: ReleaseAsset | undefined = release?.assets[assetIndex];

  const branchOptions = useMemo(() => {
    // A branch that was deleted upstream still has to be offered, otherwise the
    // dropdown would silently show something other than what is recorded.
    const names = [...branches];
    if (branch && !names.includes(branch)) names.unshift(branch);
    return [
      { data: ANY_BRANCH, label: 'Any branch' },
      ...names.map((name) => ({
        data: name,
        label: name === defaultBranch ? `${name} (default)` : name,
      })),
    ];
  }, [branches, defaultBranch, branch]);

  const releaseOptions = useMemo(
    () =>
      visible.map((r, i) => ({
        data: i,
        label: `${r.tag}${r.prerelease ? ' (pre-release)' : ''}${formatDate(r.published_at) ? ` — ${formatDate(r.published_at)}` : ''}`,
      })),
    [visible],
  );

  const assetOptions = useMemo(
    () =>
      (release?.assets ?? []).map((a, i) => ({
        data: i,
        label: formatSize(a.size) ? `${a.name} — ${formatSize(a.size)}` : a.name,
      })),
    [release],
  );

  const findReleases = async () => {
    setError('');
    setBusyLabel('Looking for releases…');
    setStep('busy');
    // The branch list is a nicety: a failure there must not stop an install, so
    // both requests go out together and only the release one can fail the step.
    const [listing, branchListing] = await Promise.all([
      getReleases(ref.owner, ref.repo, null),
      getBranches(ref.owner, ref.repo),
    ]);

    setLoadedFor(`${ref.owner}/${ref.repo}`);
    if (branchListing.ok) {
      setBranches(branchListing.branches);
      setDefaultBranch(branchListing.default_branch);
      setBranchError('');
    } else {
      setBranches([]);
      setDefaultBranch('');
      setBranchError(branchListing.error);
    }

    if (!listing.ok) {
      setError(listing.error);
      setStep('repo');
      return;
    }
    setReleases(listing.releases);
    setAssetIndex(0);
    // If we already know which version is installed, preselect a matching tag.
    const shown = listing.releases.filter((r) => releaseMatchesBranch(r, branch));
    let index = 0;
    if (existing?.installedVersion) {
      const wanted = existing.installedVersion.replace(/^v/i, '');
      const match = shown.findIndex((r) => r.tag.replace(/^v/i, '') === wanted);
      if (match >= 0) index = match;
    }
    setReleaseIndex(index);
    setStep('pick');
  };

  const pickBranch = (next: string) => {
    setBranch(next);
    setReleaseIndex(0);
    setAssetIndex(0);
  };

  const doInstall = async () => {
    if (!release || !asset) return;
    setError('');
    setBusyLabel(
      existing ? `Updating ${existing.name}…` : 'Downloading and handing off to decky…',
    );
    setStep('busy');
    const outcome = await installRelease(ref, release, asset, {
      expectedName: existing?.name,
      installType: existing ? InstallType.UPDATE : InstallType.INSTALL,
      branch,
    });
    toastOutcome(outcome, existing ? `updated to ${release.tag}` : `installed (${release.tag})`);
    if (!outcome.ok) {
      setError(outcome.error ?? 'The install failed.');
      setStep('pick');
      return;
    }
    onDone();
    closeModal?.();
  };

  const doLinkOnly = async () => {
    if (!existing || !release) return;
    setBusyLabel('Linking…');
    setStep('busy');
    await track(
      existing.name,
      ref.owner,
      ref.repo,
      release.tag,
      asset?.name ?? '',
      existing.installedVersion ?? '',
      branch,
    );
    onDone();
    closeModal?.();
  };

  const branchDescription = branchError
    ? `Branches could not be listed: ${branchError}`
    : branch
      ? `Only releases tagged from ${branch} are offered, now and when checking for updates.`
      : 'Every release counts. Pick a branch to follow just the releases cut from it.';

  return (
    <ModalRoot onCancel={closeModal} onEscKeypress={closeModal} bAllowFullSize>
      <DialogHeader>
        {existing ? `Source for ${existing.name}` : 'Add a plugin from GitHub'}
      </DialogHeader>
      <DialogBody>
        <RepoInput
          owner={owner}
          repo={repo}
          disabled={step === 'busy'}
          autoFocus={!existing}
          onChange={({ owner: o, repo: r }) => {
            setOwner(o);
            setRepo(r);
            if (step === 'pick') setStep('repo');
            // A branch belongs to the repo it was listed from, so pointing at a
            // different repo drops the pin instead of filtering by a branch name
            // that repo may not even have.
            if (`${o.trim()}/${r.trim()}` !== loadedFor) {
              setBranches([]);
              setDefaultBranch('');
              setBranchError('');
              setBranch(ANY_BRANCH);
            }
          }}
        />

        {error ? (
          <div style={{ margin: '12px 0', padding: '8px 10px', background: 'rgba(220, 80, 80, 0.15)', borderRadius: '4px', fontSize: '13px' }}>
            {error}
          </div>
        ) : null}

        {step === 'busy' ? (
          <Field label={busyLabel} bottomSeparator="none">
            <Spinner style={{ height: '28px' }} />
          </Field>
        ) : null}

        {step === 'repo' ? (
          <DialogButton disabled={!canSearch} onClick={findReleases} style={{ marginTop: '12px' }}>
            Find releases
          </DialogButton>
        ) : null}

        {step === 'pick' ? (
          <>
            <Field
              label="Branch"
              description={branchDescription}
              bottomSeparator="none"
              childrenContainerWidth="max"
            >
              <Dropdown
                rgOptions={branchOptions}
                selectedOption={branch}
                onChange={(o) => pickBranch(o.data)}
              />
            </Field>

            {release ? (
              <>
                <Field label="Release" bottomSeparator="none" childrenContainerWidth="max">
                  <Dropdown
                    rgOptions={releaseOptions}
                    selectedOption={releaseIndex}
                    onChange={(o) => {
                      setReleaseIndex(o.data);
                      setAssetIndex(0);
                    }}
                  />
                </Field>

                {assetOptions.length > 1 ? (
                  <Field
                    label="Zip"
                    description="This release has more than one zip. Pick the plugin package."
                    bottomSeparator="none"
                    childrenContainerWidth="max"
                  >
                    <Dropdown
                      rgOptions={assetOptions}
                      selectedOption={assetIndex}
                      onChange={(o) => setAssetIndex(o.data)}
                    />
                  </Field>
                ) : (
                  <Field label="Zip" bottomSeparator="none" description={asset?.name} />
                )}

                {release.notes ? (
                  <div
                    style={{
                      maxHeight: '140px',
                      overflowY: 'auto',
                      margin: '10px 0',
                      padding: '8px 10px',
                      background: 'rgba(255, 255, 255, 0.06)',
                      borderRadius: '4px',
                      fontSize: '12px',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {release.notes}
                  </div>
                ) : null}

                <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
                  <DialogButton onClick={doInstall} style={{ flex: 1 }}>
                    Install {release.tag}
                  </DialogButton>
                  {existing ? (
                    <DialogButton
                      onClick={doLinkOnly}
                      style={{ flex: 1 }}
                      // Recording the tag without reinstalling is the right move when
                      // the user already has this exact version on disk.
                    >
                      Mark as installed
                    </DialogButton>
                  ) : null}
                </div>
              </>
            ) : (
              <div style={{ margin: '12px 0', fontSize: '13px', opacity: 0.85 }}>
                None of this repo's {releases.length} release
                {releases.length === 1 ? ' was' : 's were'} tagged from {branch}. GitHub only records
                the branch a tag was cut from, so a release tagged straight from a commit matches no
                branch at all — pick another branch, or "Any branch".
              </div>
            )}
          </>
        ) : null}
      </DialogBody>
    </ModalRoot>
  );
};
