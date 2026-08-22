# Decky Bazaar

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/danielcamilo)

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for managing **every**
plugin on your Deck, including the ones that never went through the official store.

Point it at a GitHub repo, and it finds the latest release zip, installs it through Decky's own
installer, remembers the version, and tells you when a newer release shows up.

## What it does

- **Lists every installed plugin**, store-sourced or not, read straight from `~/homebrew/plugins`.
- **Adds plugins from GitHub** without typing a URL. You get `https://github.com/` followed by an
  owner field, a `/`, and a repo field. Paste a full URL into either field and it splits itself
  across both.
- **Finds the release zip for you.** It lists releases that ship a `.zip` asset, newest first, and
  reads the plugin's real name and version out of the zip before installing, so the plugin lands
  under the name Decky expects rather than a guess from the file name.
- **Tracks versions and offers updates.** Each tracked plugin records the release tag it came from.
  One button re-checks them all against GitHub.
- **Adopts plugins you already have.** Link an existing plugin to a repo and mark the release you
  are already on, without reinstalling anything.
- **Optional GitHub token** to lift the 60-requests-per-hour anonymous rate limit and reach private
  repos.

## How it installs

Bazaar does not unpack anything itself. It downloads the release zip, inspects it, and hands the
file to decky-loader's `utilities/install_plugin`. That means installs go through exactly the same
path as the official store: the standard confirmation prompt, unzip, remote binary download,
permission fixup, and hot reload. Uninstalls go through `utilities/uninstall_plugin` the same way.

A zip is only accepted if it looks like a real plugin package: exactly one top-level folder
containing a `plugin.json`. Repository source archives and multi-plugin zips are rejected with an
explanation rather than being installed into a broken state.

## Using it

Open the Quick Access menu, pick **Decky Bazaar**, and:

| Action | Where |
| --- | --- |
| Install something new | **Add plugin from GitHub** |
| Re-check every tracked plugin | **Check … for updates** |
| Update, relink, or uninstall one plugin | Tap its row in **Installed** |
| Token and pre-release options | **Settings** |

Rows sort so anything with an update waiting is at the top.

### Where state lives

Tracked sources go in `~/homebrew/settings/decky-bazaar/bazaar.json`. The GitHub token is stored in
that same file, which is chmod'd to `600`. Downloads are staged in the plugin's runtime directory
and cleared on unload.

## Building

Requires Node 16.14+ and pnpm 9+.

```bash
pnpm i
pnpm run build     # bundles src/ into dist/index.js
pnpm run typecheck # tsc --noEmit
pnpm run package   # stages the plugin folder and zips it into out/
```

`pnpm run package` produces the same `out/Decky-Bazaar-v<version>.zip` that CI attaches to a
release, so it is what to install from when testing a build by hand.

To deploy straight to a Deck over SSH instead, use the
[Decky CLI](https://github.com/SteamDeckHomebrew/cli) — `.vscode/setup.sh` fetches it, and the
`build` / `deploy` VSCode tasks drive it. Copy `.vscode/defsettings.json` to
`.vscode/settings.json` and point it at your Deck first.

### Releasing

Pushing to `main` builds, typechecks and packages the plugin. When `package.json`'s version is one
that has not been tagged yet, the workflow also cuts a `v<version>` release with the zip attached
and the matching `## v<version>` section of [CHANGELOG.md](CHANGELOG.md) as its notes. So a release
is one commit: bump the version, write the changelog section, push.

## Layout

```
main.py                      backend: GitHub API, zip inspection, tracked-source storage
src/lib/api.ts               typed callables + the decky-loader websocket bridge
src/lib/repo.ts              owner/repo parsing for typed and pasted input
src/lib/install.ts           download -> inspect -> hand to loader -> record source
src/components/RepoInput.tsx the split github.com/owner/repo field
src/components/*Modal.tsx    add/relink, per-plugin detail, settings
src/index.tsx                the Quick Access panel
```

## Caveats

- Update detection compares release **tags**. A repo that reuses or rewrites tags will look
  up to date when it isn't.
- If a plugin is updated outside Bazaar, its row is flagged as drifted rather than silently
  re-pointed.
- Only `.zip` release assets are considered, since that is what decky-loader can install.

## Support

This plugin is free and open source, and it will stay that way. If it saved you a trip through
Desktop Mode and a hand-unzipped plugin folder, you can buy me a coffee — it is genuinely
appreciated and it keeps the updates coming ☕

[![Buy me a coffee at ko-fi.com](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/danielcamilo)

Starring the repo, reporting a bug or suggesting a feature helps just as much — and costs nothing.

## License

BSD 3-Clause — see [LICENSE](LICENSE). Built on the [Decky plugin template](https://github.com/SteamDeckHomebrew/decky-plugin-template).
