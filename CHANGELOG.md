# Changelog

The section for a version is what the release workflow puts on that release's
page, so write it for someone deciding whether to update — not for someone
reading the diff.

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
