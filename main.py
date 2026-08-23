import asyncio
import json
import os
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
            if not known_tag:
                status = "unknown"
            elif latest["tag"] == known_tag:
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
                "installed_tag": known_tag,
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
