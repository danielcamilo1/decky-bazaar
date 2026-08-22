import { Focusable, TextField } from '@decky/ui';
import { CSSProperties, FC } from 'react';

import { isValidOwner, isValidRepo, looksPasted, parseRepoReference } from '../lib/repo';

export interface RepoInputProps {
  owner: string;
  repo: string;
  onChange(next: { owner: string; repo: string }): void;
  disabled?: boolean;
  autoFocus?: boolean;
}

const separatorStyle: CSSProperties = {
  alignSelf: 'center',
  padding: '0 6px',
  fontSize: '18px',
  opacity: 0.7,
  whiteSpace: 'nowrap',
};

const prefixStyle: CSSProperties = {
  ...separatorStyle,
  padding: '0 8px 0 0',
  fontSize: '15px',
  opacity: 0.55,
};

/**
 * `https://github.com/ [owner] / [repo]`, with each half its own field so a
 * controller user never has to type a URL. Pasting a full URL into either field
 * splits it across both.
 */
export const RepoInput: FC<RepoInputProps> = ({ owner, repo, onChange, disabled, autoFocus }) => {
  // Anything with a slash or colon in it is a paste, not typing: split it.
  const absorb = (raw: string): boolean => {
    if (!looksPasted(raw)) return false;
    const parsed = parseRepoReference(raw);
    if (!parsed) return false;
    onChange(parsed);
    return true;
  };

  const ownerInvalid = owner.length > 0 && !looksPasted(owner) && !isValidOwner(owner);
  const repoInvalid = repo.length > 0 && !looksPasted(repo) && !isValidRepo(repo);

  return (
    <div>
      <div style={{ fontSize: '13px', opacity: 0.7, marginBottom: '4px' }}>GitHub repository</div>
      <Focusable style={{ display: 'flex', alignItems: 'stretch', width: '100%' }} flow-children="horizontal">
        <div style={prefixStyle}>https://github.com/</div>
        <div style={{ flex: '1 1 0', minWidth: 0 }}>
          <TextField
            label="Owner"
            value={owner}
            disabled={disabled}
            focusOnMount={autoFocus}
            bShowClearAction
            description="e.g. SteamDeckHomebrew"
            onChange={(e) => {
              const raw = e.target.value;
              if (absorb(raw)) return;
              onChange({ owner: raw, repo });
            }}
          />
        </div>
        <div style={separatorStyle}>/</div>
        <div style={{ flex: '1 1 0', minWidth: 0 }}>
          <TextField
            label="Repo"
            value={repo}
            disabled={disabled}
            bShowClearAction
            description="e.g. decky-plugin-template"
            onChange={(e) => {
              const raw = e.target.value;
              if (absorb(raw)) return;
              onChange({ owner, repo: raw });
            }}
          />
        </div>
      </Focusable>
      <div style={{ fontSize: '12px', opacity: 0.6, marginTop: '6px' }}>
        {ownerInvalid
          ? 'That owner name has characters GitHub does not allow.'
          : repoInvalid
            ? 'That repo name has characters GitHub does not allow.'
            : 'Type each half, or paste a full github.com URL into either field to fill both.'}
      </div>
    </div>
  );
};
