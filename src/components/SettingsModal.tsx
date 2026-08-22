import { DialogBody, DialogButton, DialogHeader, Field, ModalRoot, TextField, ToggleField } from '@decky/ui';
import { FC, useState } from 'react';

import { BazaarSettings, setSetting, setToken } from '../lib/api';

export interface SettingsModalProps {
  closeModal?(): void;
  settings: BazaarSettings;
  hasToken: boolean;
  onDone(): void;
}

export const SettingsModal: FC<SettingsModalProps> = ({ closeModal, settings, hasToken, onDone }) => {
  const [includePrereleases, setIncludePrereleases] = useState(settings.include_prereleases);
  const [token, setTokenValue] = useState('');
  const [tokenSaved, setTokenSaved] = useState(hasToken);

  const saveToken = async (value: string) => {
    const res = await setToken(value);
    setTokenSaved(res.has_token);
    setTokenValue('');
  };

  return (
    <ModalRoot onCancel={closeModal} onEscKeypress={closeModal} onOK={() => { onDone(); closeModal?.(); }}>
      <DialogHeader>Decky Bazaar settings</DialogHeader>
      <DialogBody>
        <ToggleField
          label="Include pre-releases"
          description="Offer releases GitHub marks as pre-release when picking versions and checking for updates."
          checked={includePrereleases}
          onChange={async (value) => {
            setIncludePrereleases(value);
            await setSetting('include_prereleases', value);
          }}
        />

        <Field
          label="GitHub token"
          description={
            tokenSaved
              ? 'A token is saved. It raises the API rate limit and lets you install from private repos.'
              : 'Optional. Anonymous GitHub requests are limited to 60 per hour. A fine-grained token with read-only public repo access is enough.'
          }
          bottomSeparator="none"
        />
        <TextField
          value={token}
          bIsPassword
          bShowClearAction
          label={tokenSaved ? 'Replace saved token' : 'Paste a token'}
          onChange={(e) => setTokenValue(e.target.value)}
        />
        <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
          <DialogButton disabled={!token.trim()} onClick={() => saveToken(token)} style={{ flex: 1 }}>
            Save token
          </DialogButton>
          {tokenSaved ? (
            <DialogButton onClick={() => saveToken('')} style={{ flex: 1 }}>
              Remove token
            </DialogButton>
          ) : null}
        </div>
      </DialogBody>
    </ModalRoot>
  );
};
