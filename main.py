import asyncio
import json
import os
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
from zipfile import ZipFile

import decky

GITHUB_API = "https://api.github.com"
USER_AGENT = "decky-bazaar/0.1 (+https://github.com/SteamDeckHomebrew/decky-loader)"
SETTINGS_FILE = "bazaar.json"
STATE_VERSION = 1

# The official store builds every plugin from a git submodule in this repo, so its
# .gitmodules is an authoritative folder -> upstream repo map. Read over raw. rather
# than the contents API so it does not spend the 60-per-hour anonymous API budget
# that release checks need.
PLUGIN_DB_GITMODULES = (
    "https://raw.githubusercontent.com/SteamDeckHomebrew/decky-plugin-database/main/.gitmodules"
)
STORE_INDEX_TTL = 24 * 60 * 60

DEFAULT_SETTINGS = {
    "include_prereleases": False,
}


class GitHubError(Exception):
    """A GitHub request failed in a way worth showing the user verbatim."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub redirects asset downloads to S3, which rejects requests that still
    carry an Authorization header. Drop it when the host changes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_ssl_ctx: Optional[ssl.SSLContext] = None

# Common CA bundle locations, in the order they are worth trying on SteamOS.
_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)


def _ssl_context() -> ssl.SSLContext:
    """SteamOS's Python does not always find a CA bundle on its own, which shows up
    as "unable to get local issuer certificate". decky-loader works around this with
    certifi, and plugins inherit the loader's sys.path, so we use the same bundle and
    fall back to the system store if it is not importable."""
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx

    try:
        import certifi

        _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        decky.logger.info("Verifying TLS with the certifi bundle at %s", certifi.where())
        return _ssl_ctx
    except Exception as e:
        decky.logger.warning("certifi is unavailable (%s), falling back to the system CA store", e)

    for bundle in _CA_BUNDLES:
        if os.path.isfile(bundle):
            _ssl_ctx = ssl.create_default_context(cafile=bundle)
            decky.logger.info("Verifying TLS with the system CA bundle at %s", bundle)
            return _ssl_ctx

    _ssl_ctx = ssl.create_default_context()
    decky.logger.warning("No CA bundle found; falling back to Python's default TLS verification.")
    return _ssl_ctx


def _describe_url_error(e: urllib.error.URLError, action: str) -> str:
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (
            f"Could not {action}: the HTTPS certificate could not be verified. "
            "Your system is missing a usable CA bundle. Check the Decky Bazaar log to see which "
            "bundle it tried to use."
        )
    return f"Could not {action}: {reason}"


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()),
        _StripAuthOnRedirect(),
    )


class Plugin:
    # ------------------------------------------------------------------ state

    def _settings_path(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, SETTINGS_FILE)

    def _load_state(self) -> Dict[str, Any]:
        try:
            with open(self._settings_path(), "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        except Exception as e:
            decky.logger.error("Could not read %s, starting fresh: %s", self._settings_path(), e)
            state = {}

        state.setdefault("version", STATE_VERSION)
        state.setdefault("github_token", "")
        state.setdefault("sources", {})
        state.setdefault("store_index", {"fetched_at": 0, "entries": {}})
        settings = state.setdefault("settings", {})
        for key, value in DEFAULT_SETTINGS.items():
            settings.setdefault(key, value)
        return state

    def _save_state(self, state: Dict[str, Any]) -> None:
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        tmp = self._settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self._settings_path())

    # ----------------------------------------------------------- github calls

    def _request(self, url: str, token: str, accept: str = "application/vnd.github+json") -> Any:
        req = urllib.request.Request(url)
        req.add_header("Accept", accept)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with _opener().open(req, timeout=30) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GitHubError(self._describe_http_error(e, token), e.code) from e
        except urllib.error.URLError as e:
            raise GitHubError(_describe_url_error(e, "reach GitHub")) from e

    def _request_text(self, url: str) -> str:
        """Plain-text GET for things that are not the GitHub API — no auth header and
        no API version, so it neither needs nor spends a token."""
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "text/plain")
        try:
            with _opener().open(req, timeout=30) as res:
                return res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise GitHubError(f"GitHub returned HTTP {e.code} for the plugin database.", e.code) from e
        except urllib.error.URLError as e:
            raise GitHubError(_describe_url_error(e, "reach GitHub")) from e

    def _describe_http_error(self, e: "urllib.error.HTTPError", token: str) -> str:
        if e.code == 404:
            return (
                "Repository or release not found. Check the owner and repo name"
                + (", or add a token if the repo is private." if not token else ".")
            )
        if e.code in (401, 403):
            remaining = e.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                reset = e.headers.get("X-RateLimit-Reset")
                when = ""
                if reset and reset.isdigit():
                    when = " Resets at " + time.strftime("%H:%M", time.localtime(int(reset))) + "."
                return (
                    "GitHub rate limit reached." + when + " Add a personal access token in settings to raise the limit."
                )
            if e.code == 401:
                return "GitHub rejected the token. Check that it is valid and not expired."
            return "GitHub denied the request (403)."
        return f"GitHub returned HTTP {e.code}."

    def _download(self, url: str, token: str, dest: str) -> int:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/octet-stream")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with _opener().open(req, timeout=120) as res, open(dest, "wb") as f:
                shutil.copyfileobj(res, f)
        except urllib.error.HTTPError as e:
            raise GitHubError(self._describe_http_error(e, token), e.code) from e
        except urllib.error.URLError as e:
            raise GitHubError(_describe_url_error(e, "download the release asset")) from e
        return os.path.getsize(dest)

    # ------------------------------------------------------- installed plugins

    def _plugins_dir(self) -> str:
        return os.path.join(decky.DECKY_HOME, "plugins")

    def _read_installed(self) -> List[Dict[str, Any]]:
        root = self._plugins_dir()
        found: List[Dict[str, Any]] = []
        try:
            folders = sorted(os.listdir(root))
        except OSError as e:
            decky.logger.error("Could not list %s: %s", root, e)
            return found

        for folder in folders:
            plugin_json = os.path.join(root, folder, "plugin.json")
            if not os.path.isfile(plugin_json):
                continue
            try:
                with open(plugin_json, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception as e:
                decky.logger.warning("Skipping %s, unreadable plugin.json: %s", folder, e)
                continue

            entry: Dict[str, Any] = {
                "name": meta.get("name") or folder,
                "folder": folder,
                "author": meta.get("author", ""),
                "description": (meta.get("publish") or {}).get("description", ""),
                "version": "",
                "api_version": meta.get("api_version"),
            }
            package_json = os.path.join(root, folder, "package.json")
            try:
                with open(package_json, "r", encoding="utf-8") as f:
                    entry["version"] = json.load(f).get("version", "") or ""
            except Exception:
                pass
            found.append(entry)
        return found

    # --------------------------------------------------------- repo detection

    async def _store_index(self, state: Dict[str, Any], refresh: bool = False) -> tuple:
        """folder name -> upstream repo, as recorded by the official plugin database.
        Cached in our own state because it changes about as often as the store does.
        Returns the entries and a warning, so a network failure falls back to a stale
        cache rather than losing every detection."""
        cache = state.get("store_index") or {}
        entries = cache.get("entries") or {}
        age = int(time.time()) - int(cache.get("fetched_at") or 0)
        if entries and age < STORE_INDEX_TTL and not refresh:
            return entries, ""

        try:
            text = await asyncio.to_thread(self._request_text, PLUGIN_DB_GITMODULES)
        except GitHubError as e:
            decky.logger.warning("Could not refresh the plugin database index: %s", e)
            return entries, str(e)

        fetched = _parse_gitmodules(text)
        if not fetched:
            return entries, "The plugin database index could not be read."
        state["store_index"] = {"fetched_at": int(time.time()), "entries": fetched}
        decky.logger.info("Plugin database index refreshed: %d plugins", len(fetched))
        return fetched, ""

    def _detect_from_metadata(self, folder: str) -> Optional[Dict[str, str]]:
        """Guess a repo from the files the plugin itself ships. `publish.image` is
        usually hosted out of the plugin's own repo, and package.json often keeps a
        repository url — but plenty of plugins never edited the template's, so a
        scaffold repo is not a detection."""
        root = os.path.join(self._plugins_dir(), folder)
        meta = _read_json(os.path.join(root, "plugin.json"))
        package = _read_json(os.path.join(root, "package.json"))
        repository = package.get("repository")
        if isinstance(repository, dict):
            repository = repository.get("url")

        for text in (
            (meta.get("publish") or {}).get("image") or "",
            repository if isinstance(repository, str) else "",
            package.get("homepage") or "",
        ):
            ref = _github_ref(text)
            if ref and not _is_scaffold(ref):
                return ref
        return None

    async def _detect_all(self, state: Dict[str, Any], installed: List[Dict[str, Any]]) -> tuple:
        """Work out where each installed plugin came from. The store index is taken as
        fact; anything it does not cover falls back to the plugin's own metadata, which
        is right about nine times in ten and can name an abandoned fork the plugin was
        forked from, so the two are reported apart."""
        entries, warning = await self._store_index(state)
        detected: Dict[str, Dict[str, Any]] = {}

        for entry in installed:
            ref = entries.get((entry["folder"] or "").lower())
            origin = "store"
            if not ref:
                ref = await asyncio.to_thread(self._detect_from_metadata, entry["folder"])
                origin = "metadata"
            if not ref:
                continue
            detected[entry["name"]] = {
                "owner": ref["owner"],
                "repo": ref["repo"],
                # The submodule's branch is the one the store builds from, which is not
                # necessarily the one releases are tagged on. Pinning to it would risk
                # an automatic link that matches no release at all, so leave it open.
                "branch": "",
                "origin": origin,
            }
        return detected, warning

    # ------------------------------------------------------------- public API

    async def get_state(self) -> Dict[str, Any]:
        """Everything the panel needs for a first paint: installed plugins joined
        with whatever source we have on record for each."""
        state = self._load_state()
        sources: Dict[str, Any] = state["sources"]
        installed = await asyncio.to_thread(self._read_installed)

        for entry in installed:
            source = sources.get(entry["name"])
            entry["source"] = source
            entry["tracked"] = source is not None
            if source and source.get("version") and entry["version"]:
                entry["drifted"] = _normalize_version(source["version"]) != _normalize_version(entry["version"])
            else:
                entry["drifted"] = False

        installed_names = {entry["name"] for entry in installed}
        orphans = [
            {"name": name, "source": source}
            for name, source in sources.items()
            if name not in installed_names
        ]

        return {
            "plugins": installed,
            "orphans": orphans,
            "settings": state["settings"],
            "has_token": bool(state["github_token"]),
            "self_name": decky.DECKY_PLUGIN_NAME,
        }

    async def get_branches(self, owner: str, repo: str) -> Dict[str, Any]:
        """List the repo's branches so a plugin can be pinned to releases cut from
        one of them. The default branch is reported separately because the branch
        listing does not say which one it is."""
        state = self._load_state()
        token = state["github_token"]

        owner, repo = owner.strip(), repo.strip()
        if not owner or not repo:
            return {"ok": False, "error": "Both the owner and the repo name are required."}

        base = f"{GITHUB_API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        try:
            info = await asyncio.to_thread(self._request, base, token)
            raw = await asyncio.to_thread(self._request, base + "/branches?per_page=100", token)
        except GitHubError as e:
            return {"ok": False, "error": str(e), "status": e.status}

        default_branch = info.get("default_branch", "") or ""
        names = [b.get("name", "") for b in raw if b.get("name")]
        # The listing is capped at 100, so make sure the default is always offered.
        if default_branch and default_branch not in names:
            names.insert(0, default_branch)
        names.sort(key=lambda n: (n != default_branch, n.lower()))
        return {"ok": True, "branches": names, "default_branch": default_branch}

    async def get_releases(
        self,
        owner: str,
        repo: str,
        include_prereleases: Optional[bool] = None,
        branch: str = "",
    ) -> Dict[str, Any]:
        """List releases for a repo, keeping only the ones that ship a zip asset.
        A non-empty `branch` keeps only releases whose tag was cut from it."""
        state = self._load_state()
        setting_prereleases = bool(state["settings"]["include_prereleases"])
        if include_prereleases is None:
            include_prereleases = setting_prereleases

        owner, repo = owner.strip(), repo.strip()
        branch = (branch or "").strip()
        if not owner or not repo:
            return {"ok": False, "error": "Both the owner and the repo name are required."}

        url = f"{GITHUB_API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/releases?per_page=30"
        try:
            raw = await asyncio.to_thread(self._request, url, state["github_token"])
        except GitHubError as e:
            return {"ok": False, "error": str(e), "status": e.status}

        releases = []
        hidden_prereleases = 0
        for release in raw:
            if release.get("draft"):
                continue
            # Branch first: a pre-release on some other branch was never on offer,
            # so counting it as "hidden by the pre-release setting" would send the
            # user to a toggle that changes nothing.
            if branch and not _release_matches_branch(release, branch):
                continue
            if release.get("prerelease") and not include_prereleases:
                hidden_prereleases += 1
                continue
            assets = [
                {
                    "name": a["name"],
                    "size": a.get("size", 0),
                    "download_url": a.get("browser_download_url", ""),
                    "api_url": a.get("url", ""),
                }
                for a in release.get("assets", [])
                if a.get("name", "").lower().endswith(".zip")
            ]
            if not assets:
                continue
            releases.append(
                {
                    "tag": release.get("tag_name", ""),
                    "title": release.get("name") or release.get("tag_name", ""),
                    "target_commitish": release.get("target_commitish", "") or "",
                    "published_at": release.get("published_at", ""),
                    "prerelease": bool(release.get("prerelease")),
                    "notes": (release.get("body") or "")[:2000],
                    "assets": assets,
                }
            )

        # Highest version first, so releases[0] is the latest release and the
        # picker lists them in the order someone expects to read them.
        releases.sort(key=_release_sort_key, reverse=True)

        if not releases:
            if hidden_prereleases:
                plural = "" if hidden_prereleases == 1 else "s"
                where = f' on "{branch}"' if branch else ""
                return {
                    "ok": False,
                    "error": (
                        f"{hidden_prereleases} release{plural}{where} {'is' if hidden_prereleases == 1 else 'are'} "
                        "marked pre-release and hidden. Turn on \u201cInclude pre-releases\u201d to use them."
                    ),
                    "hidden_prereleases": hidden_prereleases,
                }
            if branch:
                return {
                    "ok": False,
                    "error": (
                        f'No release with a .zip asset was cut from the "{branch}" branch. GitHub only '
                        "records which branch a tag came from, so a release tagged straight from a commit "
                        "will not match any branch."
                    ),
                }
            return {
                "ok": False,
                "error": "No release with a .zip asset was found. Decky plugins are published as a zip on the releases page.",
            }
        return {
            "ok": True,
            "releases": releases,
            # The stored setting, not the argument: callers that asked for every
            # release still need to know what the user's default is.
            "include_prereleases": setting_prereleases,
            "hidden_prereleases": hidden_prereleases,
        }

    async def stage_asset(self, download_url: str, api_url: str = "", asset_name: str = "plugin.zip") -> Dict[str, Any]:
        """Download a release zip and read the plugin name and version out of it,
        so we install under the plugin's real name rather than guessing from the
        file name. Returns a file:// artifact for decky's own installer."""
        state = self._load_state()
        token = state["github_token"]
        # Private-repo assets are only reachable through the API url with a token.
        url = api_url if (token and api_url) else download_url
        if not url:
            return {"ok": False, "error": "That release asset has no download URL."}

        staging = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "staging")
        await asyncio.to_thread(shutil.rmtree, staging, True)
        await asyncio.to_thread(os.makedirs, staging, 0o755, True)
        dest = os.path.join(staging, os.path.basename(asset_name) or "plugin.zip")

        try:
            size = await asyncio.to_thread(self._download, url, token, dest)
        except GitHubError as e:
            return {"ok": False, "error": str(e), "status": e.status}

        try:
            info = await asyncio.to_thread(_inspect_plugin_zip, dest)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        info.update({"ok": True, "artifact": "file://" + dest, "size": size})
        return info

    async def track(
        self,
        plugin_name: str,
        owner: str,
        repo: str,
        tag: str = "",
        asset_name: str = "",
        version: str = "",
        branch: str = "",
    ) -> Dict[str, Any]:
        """Record where a plugin came from so we can check it for updates."""
        state = self._load_state()
        state["sources"][plugin_name] = {
            "owner": owner.strip(),
            "repo": repo.strip(),
            "branch": (branch or "").strip(),
            "tag": tag,
            "asset_name": asset_name,
            "version": version,
            "updated_at": int(time.time()),
            "origin": "manual",
        }
        self._save_state(state)
        decky.logger.info(
            "Tracking %s at %s/%s%s (%s)",
            plugin_name,
            owner,
            repo,
            f"@{branch.strip()}" if (branch or "").strip() else "",
            tag or "unknown tag",
        )
        return {"ok": True}

    async def untrack(self, plugin_name: str) -> Dict[str, Any]:
        state = self._load_state()
        if state["sources"].pop(plugin_name, None) is not None:
            self._save_state(state)
        return {"ok": True}

    async def auto_link(self, only_untracked: bool = True) -> Dict[str, Any]:
        """Link plugins to the repo they came from without the user typing anything.
        Only the release tag is left blank: nothing here knows which release is on
        disk, so the next update check settles that by version instead."""
        state = self._load_state()
        sources = state["sources"]
        installed = await asyncio.to_thread(self._read_installed)
        if only_untracked:
            installed = [p for p in installed if p["name"] not in sources]
        else:
            # A repo the user typed in themselves outranks anything detected here.
            installed = [p for p in installed if (sources.get(p["name"], {}).get("origin") != "manual")]

        detected, warning = await self._detect_all(state, installed)
        by_name = {p["name"]: p for p in installed}

        linked: List[Dict[str, Any]] = []
        for name, ref in detected.items():
            previous = sources.get(name) or {}
            # Re-detecting the same repo must not throw away which release we know is
            # installed; only a move to a different repo invalidates that.
            same_repo = (
                previous.get("owner", "").lower() == ref["owner"].lower()
                and previous.get("repo", "").lower() == ref["repo"].lower()
            )
            sources[name] = {
                "owner": ref["owner"],
                "repo": ref["repo"],
                "branch": previous.get("branch", "") if same_repo else ref["branch"],
                "tag": previous.get("tag", "") if same_repo else "",
                "asset_name": previous.get("asset_name", "") if same_repo else "",
                "version": by_name[name].get("version", ""),
                "updated_at": int(time.time()),
                "origin": ref["origin"],
            }
            linked.append(dict(ref, name=name))
            decky.logger.info(
                "Linked %s to %s/%s (from the %s)",
                name,
                ref["owner"],
                ref["repo"],
                "plugin database" if ref["origin"] == "store" else "plugin's own metadata",
            )

        # Saved even when nothing was linked, so a freshly fetched store index is kept.
        self._save_state(state)
        return {
            "ok": True,
            "linked": linked,
            "considered": len(installed),
            "warning": warning,
        }

    async def check_updates(self) -> Dict[str, Any]:
        """Check every tracked plugin against its repo's latest usable release."""
        state = self._load_state()
        include_prereleases = state["settings"]["include_prereleases"]
        installed = {p["name"]: p for p in await asyncio.to_thread(self._read_installed)}

        results: Dict[str, Any] = {}
        for name, source in state["sources"].items():
            if name not in installed:
                continue
            branch = source.get("branch", "") or ""
            listing = await self.get_releases(source["owner"], source["repo"], include_prereleases, branch)
            if not listing["ok"]:
                results[name] = {"status": "error", "error": listing["error"], "branch": branch}
                continue

            latest = listing["releases"][0]
            asset = _pick_asset(latest["assets"], source.get("asset_name", ""))
            known_tag = source.get("tag", "")
            # An automatically linked plugin has no recorded tag, so fall back to the
            # version on disk: comparing that against the latest tag is what makes the
            # link useful without asking the user to name the release they are on.
            installed_version = installed[name].get("version", "")
            known_ref = known_tag or installed_version
            latest_key, known_key = _version_key(latest["tag"]), _version_key(known_ref)
            if not known_ref or known_key is None:
                status = "unknown"
            elif latest["tag"] == known_tag:
                status = "current"
            elif latest_key is not None and known_key is not None and latest_key <= known_key:
                # Nothing on offer is newer than what is installed — a release cut
                # for an older line, or one pulled after we recorded a newer tag.
                # Offering it would be a downgrade dressed up as an update.
                status = "current"
            else:
                status = "update"
            results[name] = {
                "status": status,
                "latest_tag": latest["tag"],
                "latest_title": latest["title"],
                "published_at": latest["published_at"],
                "prerelease": latest["prerelease"],
                "notes": latest["notes"],
                "asset": asset,
                "installed_tag": known_ref,
                "branch": branch,
            }

        state["last_checked"] = int(time.time())
        self._save_state(state)
        return {"ok": True, "results": results, "checked_at": state["last_checked"]}

    async def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        state = self._load_state()
        if key not in DEFAULT_SETTINGS:
            return {"ok": False, "error": f"Unknown setting {key}"}
        state["settings"][key] = value
        self._save_state(state)
        return {"ok": True, "settings": state["settings"]}

    async def set_token(self, token: str) -> Dict[str, Any]:
        state = self._load_state()
        state["github_token"] = token.strip()
        self._save_state(state)
        try:
            os.chmod(self._settings_path(), 0o600)
        except OSError as e:
            decky.logger.warning("Could not tighten permissions on the settings file: %s", e)
        return {"ok": True, "has_token": bool(state["github_token"])}

    # ------------------------------------------------------------- lifecycle

    async def _main(self):
        decky.logger.info("Decky Bazaar started, watching %s", self._plugins_dir())

    async def _unload(self):
        await asyncio.to_thread(shutil.rmtree, os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "staging"), True)
        decky.logger.info("Decky Bazaar unloaded")

    async def _uninstall(self):
        decky.logger.info("Decky Bazaar uninstalled")


def _normalize_version(value: str) -> str:
    return value.strip().lstrip("vV")


def _version_key(value: str) -> Optional[tuple]:
    """Order tags the way a human reads them, not the way strings compare: 1.0.10
    comes after 1.0.9, and 1.1.0-rc1 comes before 1.1.0. Returns None for a tag
    with no number in it at all, which nothing can be ordered against.

    Segments are compared as (kind, value) pairs so a numeric segment never has
    to be compared against a word — 1.0 sorts below 1.0.1 and below 1.0.final
    without raising."""
    tag = _normalize_version(value).split("+", 1)[0]
    if not tag or not any(c.isdigit() for c in tag):
        return None
    core, _, pre = tag.partition("-")

    def segments(text: str) -> tuple:
        parts = []
        for chunk in re.split(r"[.\-_]", text):
            if not chunk:
                continue
            # Split runs of digits from the letters around them, so rc1 orders
            # after rc and before rc2.
            for piece in re.findall(r"\d+|\D+", chunk):
                parts.append((0, int(piece)) if piece.isdigit() else (1, piece.lower()))
        return tuple(parts)

    # A release outranks any pre-release of the same version, so the absence of a
    # suffix has to sort higher than every suffix there is.
    return (segments(core), (0, segments(pre)) if pre else (1, ()))


def _release_sort_key(release: Dict[str, Any]) -> tuple:
    """Newest first once reversed. GitHub lists releases by when they were
    created, which is not the same as which version is highest: a patch cut for
    an older line, or a release published out of order, would otherwise be taken
    for the latest. Tags that carry no version fall back to the publish date."""
    key = _version_key(release.get("tag", ""))
    return ((1, key) if key is not None else (0, ()), release.get("published_at") or "")


# github.com covers both https and scp-style remotes; raw. and opengraph. cover the
# image urls plugins point at their own repo. The owner and repo classes are GitHub's
# own rules, so a trailing path segment cannot be mistaken for part of the name.
_GITHUB_URL_RE = re.compile(
    r"(?:raw\.githubusercontent\.com|opengraph\.githubassets\.com/1|github\.com)"
    r"[:/]+([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)

# Repos a plugin can end up naming without ever meaning to: the template it was
# generated from still carries its own repository url in package.json, and the
# template's plugin.json points publish.image at the loader.
_SCAFFOLD_REPOS = {
    ("steamdeckhomebrew", "decky-plugin-template"),
    ("steamdeckhomebrew", "pluginloader"),
    ("steamdeckhomebrew", "decky-loader"),
    ("steamdeckhomebrew", "decky-frontend-lib"),
    ("steamdeckhomebrew", "decky-plugin-database"),
}


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _github_ref(text: str) -> Optional[Dict[str, str]]:
    """Pull the first owner/repo out of any GitHub url found in the text."""
    match = _GITHUB_URL_RE.search(text or "")
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    if not repo or repo in (".", ".."):
        return None
    return {"owner": owner, "repo": repo}


def _is_scaffold(ref: Dict[str, str]) -> bool:
    return (ref["owner"].lower(), ref["repo"].lower()) in _SCAFFOLD_REPOS


def _parse_gitmodules(text: str) -> Dict[str, Dict[str, str]]:
    """Turn the plugin database's .gitmodules into folder -> repo. The submodule path
    is the folder the plugin installs under, which is what makes this a usable key.
    Plugins hosted somewhere other than GitHub are left out: Bazaar cannot follow
    their releases, and a wrong guess is worse than no link."""
    entries: Dict[str, Dict[str, str]] = {}
    path = url = branch = ""

    def flush() -> None:
        nonlocal path, url, branch
        ref = _github_ref(url) if path and url else None
        if ref:
            entries[path.rsplit("/", 1)[-1].lower()] = dict(ref, branch=branch)
        path = url = branch = ""

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[submodule"):
            flush()
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not value:
            continue
        if key == "path":
            path = value
        elif key == "url":
            url = value
        elif key == "branch":
            branch = value
    flush()
    return entries


def _release_matches_branch(release: Dict[str, Any], branch: str) -> bool:
    """GitHub records the branch a release's tag was cut from in `target_commitish`.
    It holds a raw commit SHA when the tag was made from a commit rather than a
    branch, which no branch name can match."""
    return (release.get("target_commitish") or "") == branch


def _pick_asset(assets: List[Dict[str, Any]], preferred_name: str) -> Optional[Dict[str, Any]]:
    """Prefer the asset named like the one we installed last time, so repos that
    publish several zips stay on the same artifact across updates."""
    if not assets:
        return None
    if preferred_name:
        for asset in assets:
            if asset["name"] == preferred_name:
                return asset
    return assets[0]


def _inspect_plugin_zip(path: str) -> Dict[str, Any]:
    """Pull the plugin name and version out of a decky plugin zip. Mirrors the
    layout decky-loader expects: a single top-level folder holding plugin.json."""
    with ZipFile(path) as archive:
        names = archive.namelist()
        plugin_jsons = [n for n in names if n.endswith("/plugin.json") and n.count("/") == 1]
        if not plugin_jsons:
            raise ValueError(
                "This zip has no plugin.json in a top-level folder, so it is not a decky plugin package."
            )
        if len(plugin_jsons) > 1:
            raise ValueError("This zip contains more than one plugin, which decky cannot install.")

        entry = plugin_jsons[0]
        root = entry.split("/")[0]
        with archive.open(entry) as f:
            meta = json.loads(f.read().decode("utf-8"))

        name = (meta.get("name") or "").strip() or root
        version = ""
        try:
            with archive.open(f"{root}/package.json") as f:
                version = (json.loads(f.read().decode("utf-8")).get("version") or "").strip()
        except KeyError:
            pass

        return {
            "plugin_name": name,
            "plugin_version": version,
            "root": root,
            "author": meta.get("author", ""),
            "description": (meta.get("publish") or {}).get("description", ""),
        }
