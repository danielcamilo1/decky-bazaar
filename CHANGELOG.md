# Changelog

The section for a version is what the release workflow puts on that release's
page, so write it for someone deciding whether to update — not for someone
reading the diff.

## v1.1.0

Plugins can now follow a single branch of their repo instead of every release.

- **Pick a branch when adding or re-pointing a plugin.** The Add and Source
  dialogs list the repo's branches, with the default branch first. Only releases
  whose tag was cut from the chosen branch are offered — and every later update
  check stays on that branch, so a plugin set to `beta` never offers a `main`
  release and the other way round.
- **Any branch** is still the default, so plugins tracked before this update
  keep considering every release until you narrow them down.
- The panel row and the plugin's Source line now show which branch is being
  followed, as `owner/repo@branch`.

Note that GitHub only records the branch a tag was *cut from*. A release tagged
straight from a commit has no branch attached and shows up only under **Any
branch**.

## v1.0.0

First release.

Decky Bazaar lists **every** plugin installed on your Deck — store-sourced or
not — and keeps the ones that never went through the official store updated
from their GitHub releases.

- **Add a plugin from GitHub** by owner and repo rather than a URL. Paste a full
  URL into either field and it splits itself across both.
- **Finds the release zip for you**, newest first, and reads the plugin's real
  name and version out of the zip before installing, so it lands under the name
  Decky expects instead of a guess from the file name.
- **Installs through decky-loader itself** — the same confirmation prompt,
  unzip, remote binary download, permission fixup and hot reload the official
  store uses. A zip is only accepted if it looks like a real plugin package, so
  source archives and multi-plugin zips are refused with an explanation rather
  than installed into a broken state.
- **Tracks versions and offers updates.** One button re-checks every tracked
  plugin against GitHub, and rows with an update waiting sort to the top.
- **Adopts plugins you already have.** Link an installed plugin to a repo and
  mark the release you are on, without reinstalling it.
- **Optional GitHub token** to lift the 60-requests-per-hour anonymous rate
  limit and reach private repos. It is stored in
  `~/homebrew/settings/decky-bazaar/bazaar.json`, chmod'd to `600`.
