export interface RepoRef {
  owner: string;
  repo: string;
}

// GitHub's own rules: owners are alphanumeric with single hyphens, repos also
// allow dots and underscores.
const OWNER_RE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;
const REPO_RE = /^[A-Za-z0-9._-]{1,100}$/;

export function isValidOwner(owner: string): boolean {
  return OWNER_RE.test(owner);
}

export function isValidRepo(repo: string): boolean {
  return REPO_RE.test(repo) && repo !== '.' && repo !== '..';
}

function stripGitSuffix(repo: string): string {
  return repo.endsWith('.git') ? repo.slice(0, -4) : repo;
}

/**
 * Pull an owner/repo pair out of whatever the user typed or pasted: a bare
 * `owner/repo`, a full https URL, an scp-style git remote, or a deep link to a
 * releases page. Returns null when the text is not a repo reference, which is
 * the normal case while someone is still typing an owner.
 */
export function parseRepoReference(input: string): RepoRef | null {
  let text = input.trim();
  if (!text) return null;

  // git@github.com:owner/repo.git
  const scp = text.match(/^git@github\.com:(.+)$/i);
  if (scp) {
    text = scp[1];
  } else {
    text = text.replace(/^git\+/i, '');
    // https://github.com/owner/repo, github.com/owner/repo, www.github.com/...
    const web = text.match(/^(?:https?:\/\/)?(?:www\.)?github\.com\/(.+)$/i);
    if (web) {
      text = web[1];
    } else if (/^https?:\/\//i.test(text)) {
      // A URL, but not a GitHub one.
      return null;
    }
  }

  const [pathPart] = text.split(/[?#]/, 1);
  const segments = pathPart.split('/').filter(Boolean);
  if (segments.length < 2) return null;

  const owner = segments[0];
  const repo = stripGitSuffix(segments[1]);
  if (!isValidOwner(owner) || !isValidRepo(repo)) return null;
  return { owner, repo };
}

/** True when the text looks like a URL or a pasted `owner/repo`, rather than a
 * plain owner name someone is halfway through typing. */
export function looksPasted(input: string): boolean {
  return input.includes('/') || input.includes(':');
}

export function repoUrl({ owner, repo }: RepoRef): string {
  return `https://github.com/${owner}/${repo}`;
}
