#!/usr/bin/env python3
"""The FORGE seam: what fleet needs to know about a proposed change, and
nothing about which forge answers it.

WHY THIS EXISTS. Fleet's queue used to run `gh` inline in six places and build
`https://github.com/...` in a seventh, so "the forge" and "GitHub" were the
same word. They are not: the operator's fleet runs on GitHub *today*, and that
is a configuration, not a fact about the model. This module is the line between
the two. Above it, `queue.py` asks questions; below it, one adapter per forge
answers them with whatever CLI or API that forge has.

THE WORD. A GitHub *pull request* and a GitLab *merge request* are the same
thing to this code, so the code says CHANGE REQUEST and never picks a side.
Prose that is genuinely about GitHub still says "pull request", because there
it is describing GitHub. `ChangeRequest` below is the whole vocabulary.

THE QUESTIONS, and they are the entire interface. Each one is something fleet
actually decides on, and nothing here exists because a forge happens to offer
it:

    parse_change_url        is this URL a change request, and which one
    repo_from_remote        which repository is this checkout's `origin`
    get                     one change request in full: what `collect` needs to
                            check a publish claim — the body carrying the
                            no-mistakes attestation, the head commit it must
                            name, the branch it is open from, and the commits
                            that grew the head since
    state                   open / merged / closed, for the landing check. A
                            second, narrower question than `get` on purpose:
                            `reap` sweeps every concluded task on a timer and
                            must not pay for a body it will not read
    open_change_requests    every open change request on a repository
    open_change_requests_in_checkout
                            the same, asked of a local checkout rather than of
                            a repository id — what `fleet-status.sh` needs and
                            the only caller that has a path but no identity
    can_push                may this account push to this repository — the last
                            gate before an unattended merge
    merge                   merge it, by a named method

REPOSITORY IDENTITY CARRIES A HOST. `Thurbeen/fleet` names two different
repositories if two forges are configured, and self-hosted instances are the
NORMAL case for everything that is not github.com. So a repository is a
`RepoId(host, path)` and `AUTO_MERGE_REPOS` in queue.py is spelled
`github.com/Thurbeen/fleet` — an entry that names no host is refused rather
than guessed at.

EVERY ANSWER CAN BE "I COULD NOT TELL". Each method returns its answer beside a
non-empty string saying why it could not be had, and no caller is allowed to
collapse that string into a verdict. A timeout must never be able to
manufacture a merge.

WHAT SHIPS. Two adapters: GitHub through `gh`, GitLab through `glab`. Both are
CONFIGURATION — a self-hosted instance is the normal case for everything that
is not github.com or gitlab.com, so which hosts an adapter owns is read off the
machine rather than assumed. For GitLab that is `configured_hosts` below: the
instances `glab auth status` reports, which is where the operator's answer
already lives. `GITLAB_HOST` still decides when it is set, and the GitHub
adapter still takes `GH_HOST` alone — see `configured_hosts` for why the same
discovery is not done for `gh`.

ADDING A FORGE. Write a class with the methods below and register it: either
in `BUILTIN` here, or — for a test, or a forge that is not fleet's business to
ship — through `FLEET_FORGE_PLUGINS`, a colon-separated list of Python files
each exporting `forges()`. `scripts/queue-selftest.sh` drives the whole queue
through a plugin with no network and no `gh` behind it, which is how this seam
is proved rather than asserted.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field

# --- identity ----------------------------------------------------------------


@dataclass(frozen=True)
class RepoId:
    """One repository, on one forge. The host is half of the name.

    `path` is whatever that forge puts after the host — `owner/repo` on GitHub
    and Gitea, `group/subgroup/project` on GitLab. Nothing here parses it apart
    from `owner`, which is the first segment on every forge fleet has met.
    """

    host: str
    path: str

    def __str__(self) -> str:
        # Reads as a sentence in a report: "... on many-owner/many-repo on
        # github.com". `qualified` is the form for config and for JSON.
        return f"{self.path} on {self.host}"

    @property
    def qualified(self) -> str:
        """`github.com/Thurbeen/fleet` — the form config files are written in."""
        return f"{self.host}/{self.path}"

    @property
    def owner(self) -> str:
        return self.path.split("/", 1)[0]

    @classmethod
    def parse(cls, text: str) -> RepoId | None:
        """A host-qualified repository, or None for anything else.

        A bare `owner/repo` is REFUSED and not guessed at. It is ambiguous the
        moment a second forge exists, and the config that used to be written
        that way is the auto-merge allowlist — the one place where guessing
        wrong means acting on somebody else's repository.
        """
        parts = [p for p in str(text or "").strip().split("/") if p]
        if len(parts) < 3 or "." not in parts[0]:
            return None
        return cls(parts[0].lower(), "/".join(parts[1:]))


@dataclass(frozen=True)
class ChangeRef:
    """A change request, named rather than fetched: which repository, and which number."""

    repo: RepoId
    number: int
    url: str

    def __str__(self) -> str:
        return self.url


@dataclass(frozen=True)
class Commit:
    """One commit on a change request's head branch, oldest first.

    Read for ONE thing: telling the pipeline's own follow-up push apart from
    somebody else pushing over it, which is the same refusal with two different
    remedies. A forge that cannot enumerate them answers with an empty list,
    and an empty list must never become a claim about who pushed what.
    """

    sha: str
    headline: str


@dataclass(frozen=True)
class Check:
    """One CI check, in fleet's own words rather than each forge's vocabulary.

    `pending` is never `failed`: reading a check that has not finished as a
    broken one is how a shepherd spawns fixers for healthy change requests, and
    reading it as passed is how it merges one whose CI never ran. `cancelled`
    is its own word rather than folded into either: a check somebody called
    off is not a passing one, but the shepherd and fleet-status have always
    disagreed about whether it blocks a merge — see `GH_CHECK_FAILED` below —
    and a shared verdict must let both keep their own answer.
    """

    name: str
    verdict: str  # "passed" | "failed" | "pending" | "cancelled"


@dataclass
class ChangeRequest:
    """One proposed change, normalised. Every field is fleet's word, not a forge's.

    An empty string means THE FORGE DID NOT SAY, everywhere. Callers that act
    on a field check it rather than defaulting it — `head_is_ours` is `None`
    for "could not tell" for exactly that reason, and `classify` treats that as
    undetermined rather than as a stranger or as one of ours.
    """

    ref: ChangeRef
    title: str = ""
    state: str = ""  # "open" | "merged" | "closed" | "" (not said)
    draft: bool = False
    body: str = ""
    head_branch: str = ""
    base_branch: str = ""
    head_sha: str = ""
    author: str = ""
    author_is_bot: bool = False
    mergeable: str = ""  # "mergeable" | "conflicting" | "" (not said / still computing)
    review_decision: str = ""  # "changes-requested" | "approved" | "" (not said)
    checks: list = field(default_factory=list)
    commits: list = field(default_factory=list)
    # Is the head branch inside the target repository? The one claim about a
    # change request that whoever opened it cannot write for themselves, and
    # the only forge-specific judgement fleet delegates rather than derives:
    # a fork, a mirror and a same-repo branch are told apart differently on
    # every forge. `None` means the forge did not say.
    head_is_ours: bool | None = None
    head_location: str = ""  # a phrase naming where the head branch lives

    @property
    def repo(self) -> RepoId:
        return self.ref.repo

    @property
    def number(self) -> int:
        return self.ref.number

    @property
    def url(self) -> str:
        return self.ref.url

    @property
    def name(self) -> str:
        """`Thurbeen/fleet#13` — short enough for a status line."""
        return f"{self.repo.path}#{self.number}"


# The shape of a change request URL on any forge fleet has met: GitHub and
# Gitea end in `/pull/<n>`, GitLab in `/-/merge_requests/<n>`. This answers
# only "is that artifact a change request at all" — which forge OWNS it, and
# whether that forge is configured, are separate questions with separate
# answers, so that an artifact on a forge nobody configured reads as "could
# not be checked" rather than as "the worker shipped nothing".
CHANGE_URL_RE = re.compile(
    r"^(https?://[^/\s]+/[^/\s]+(?:/[^/\s]+)+?/(?:pull|merge_requests)/\d+)"
    r"(?:[/?#].*)?$"
)


def change_url(url) -> str:
    """The canonical change-request URL inside `url`, or '' if it is not one.

    Group 1 rather than the whole string, so a link someone pasted with
    `/files` or a `#comment` on the end still resolves to what it names.
    """
    m = CHANGE_URL_RE.match((url or "").strip())
    return m.group(1) if m else ""


# --- which hosts a CLI is configured for -------------------------------------


# How both `gh auth status` and `glab auth status` head each instance they are
# configured for: the bare hostname, alone on an unindented line, with
# everything they have to say about it indented underneath. A `:port` is
# allowed because a self-hosted instance on one is ordinary and `RepoId`
# carries the port as part of the host. A dot is REQUIRED, for the same reason
# `RepoId.parse` requires one: it is what tells a hostname from a decoration
# line, and it is the shape every host fleet can be handed as part of a URL.
AUTH_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9-]+(?::\d+)?$")


def configured_hosts(cli: str, timeout: int = 10) -> list:
    """Every instance `cli` is authenticated or configured for, asked of `cli`.

    WHY THIS EXISTS. `GH_HOST` and `GITLAB_HOST` are the documented way to tell
    fleet about a self-hosted instance, and in practice nothing exports them:
    an operator logs their CLI in once and never thinks about it again. The
    result was that a whole forge was invisible — `shepherd` never saw a merge
    request on a self-hosted instance, `collect` could not verify a publish
    there, and `reap` could never land the task, so its session and worktree
    leaked with no upper bound. The operator's answer was already on the
    machine; nothing was reading it.

    IT NEEDS NO NETWORK. `auth status` prints one heading per configured
    instance out of the CLI's own config and then decorates each with an API
    call, so the headings are there whether or not the call succeeds — measured
    on 2026-09-11 against `glab` 1.117.0 with every request refused, which
    printed both instances in 0.2s. The exit code is ignored for the same
    reason: `glab` exits non-zero when ANY one instance fails to authenticate,
    which is the ordinary state of a machine logged in to one instance and not
    the other.

    BOTH STREAMS ARE READ, because they disagree: `glab` writes the whole
    report to stderr and `gh` writes it to stdout.

    IT NEVER FAILS. No CLI, no config, a report it cannot parse, or a CLI too
    old for `--all` each answer with an empty list, which leaves every caller
    exactly where it was before discovery existed. Discovery is an improvement
    on a default, never a dependency: `collect` has to keep working with the
    network down and on a machine that has neither CLI.

    WHY THE GITHUB ADAPTER DOES NOT USE THIS, though `gh auth status` prints
    the same shape and GitHub Enterprise is the same problem. §13 of
    `queue-selftest.sh` drives the whole queue through a forge that is not
    GitHub with `gh` on PATH as a TRIPWIRE — it fails on any invocation at all,
    so code reaching around this seam shows up there by name. Building the
    GitHub adapter would run `gh auth status` and trip it, and the honest
    choice between "discover GitHub Enterprise" and "keep the regression test
    that keeps this seam honest" is the second one: `GH_HOST` was never the
    half that was broken. This function takes the CLI by name so that
    everything else — `scripts/preflight.sh` reporting auth per host, say —
    can ask it about `gh` too, and so that the day that tripwire can tell a
    configuration read from a change-request call, the adapter needs one line.
    """
    cli = str(cli or "").strip()
    hosts: list = []
    if cli and shutil.which(cli):
        # `--all` is the documented way to ask about every instance rather than
        # the one the current directory implies. A CLI too old to know the flag
        # refuses the whole command, so the bare form is tried after it — which
        # on a machine with no git context answers the same thing. A call that
        # never answered at all stops the sequence rather than being retried:
        # the second ask would hang exactly as long as the first, and paying
        # the timeout twice is how discovery would start costing `collect`
        # real time on the flaky network it is supposed to survive.
        for argv in ([cli, "auth", "status", "--all"], [cli, "auth", "status"]):
            said = _auth_status_hosts(argv, timeout)
            if said is None:
                break
            if said:
                hosts = said
                break
    return hosts


def _auth_status_hosts(argv: list, timeout: int):
    """The hostnames `argv` printed, or None if it never answered at all."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    seen: dict = {}
    for line in ((out.stdout or "") + "\n" + (out.stderr or "")).splitlines():
        if not line[:1].strip():  # every heading is unindented; the rest is not
            continue
        host = line.strip().lower()
        if AUTH_HOST_RE.match(host):
            seen[host] = True
    return list(seen)


# --- the interface -----------------------------------------------------------


class Forge:
    """One forge. Subclass, implement, register.

    The base class answers "I cannot" to everything, so a partial adapter
    degrades into "could not be determined" — which every caller already
    handles — instead of raising into the middle of a shepherd pass.
    """

    name = "forge"
    hosts: tuple = ()
    # The merge methods this forge can actually perform. A caller asking for
    # one that is not here is told so BEFORE anything is merged: a GitLab
    # project can forbid squash, and "the forge refused this merge method" has
    # to be a sentence fleet can say.
    merge_methods: tuple = ()

    def owns_host(self, host: str) -> bool:
        return (host or "").lower() in self.hosts

    def parse_change_url(self, url: str) -> ChangeRef | None:
        return None

    def repo_from_remote(self, remote_url: str) -> RepoId | None:
        return None

    def get(self, ref: ChangeRef) -> tuple:
        return None, f"{self.name} cannot read a change request"

    def state(self, ref: ChangeRef) -> tuple:
        return None, f"{self.name} cannot read a change request state"

    def open_change_requests(self, repo: RepoId) -> tuple:
        return [], f"{self.name} cannot list change requests"

    def open_change_requests_in_checkout(self, path: str) -> tuple:
        return [], f"{self.name} cannot list change requests"

    def can_push(self, repo: RepoId, login: str) -> tuple:
        return False, f"{self.name} cannot say who may push to {repo}"

    def describe_merge(self, method: str, delete_branch: bool) -> str:
        """What this forge would run, for a dry run to print."""
        return f"{self.name}: merge by {method}"

    def merge(self, cr: ChangeRequest, method: str, delete_branch: bool) -> tuple:
        return False, f"{self.name} cannot merge"


# --- GitHub, the first implementation ----------------------------------------


# `gh pr list --limit` is a request cap, not a page size — gh paginates the
# GraphQL calls itself to reach it. Set high enough that hitting it means the
# repository genuinely has that many open pull requests, which the caller then
# treats as unreadable rather than silently returning a truncated list.
GH_LIST_LIMIT = 1000

# One `gh pr list` answers every question the shepherd asks, so a pass costs
# one call per repository rather than one per pull request. The last four are
# the safety fields: `headRefOid` is what an attestation has to name, and the
# other three are how a fork's pull request is told from ours.
GH_FIELDS = (
    "number,state,url,title,isDraft,mergeable,reviewDecision,"
    "statusCheckRollup,body,headRefName,baseRefName,headRefOid,"
    "author,headRepositoryOwner,isCrossRepository"
)

# The narrower set `fleet-status.sh` needs: it prints a line per pull request
# and decides nothing, so it does not pay for the safety fields.
GH_STATUS_FIELDS = "number,url,title,headRefName,state,statusCheckRollup"

# What ONE change request costs when `collect` checks a publish claim. The head
# branch is in there because it is the one claim about a pull request a worker
# cannot write into its own result.md, and it arrives free with the body.
GH_ONE_FIELDS = "body,headRefOid,headRefName,state,commits"

# A check that FAILED. Anything still running is NOT a failure. `CANCELLED` is
# its own conclusion, mapped to the `cancelled` verdict rather than in here:
# it is absent from the shepherd's list and present in fleet-status's, both
# already so before this module existed, and this keeps that difference alive
# instead of erasing it onto whichever caller happened to read second.
GH_CHECK_FAILED = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "ERROR"}
GH_CHECK_PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
GH_CHECK_CANCELLED = {"CANCELLED"}

GH_URL_RE = re.compile(
    r"^https?://([^/\s]+)/([^/\s]+/[^/\s]+?)(?:\.git)?/pull/(\d+)(?:[/?#].*)?$"
)
GH_REMOTE_RE = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+)[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")

# GitHub's own state words, mapped onto fleet's three. Anything else is not
# translated into a guess: the caller is told the forge said something this
# adapter does not know, which is `unknown` and never `open`.
GH_STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}

GH_PUSH_PERMISSIONS = {"admin", "maintain", "write"}


class GitHubForge(Forge):
    """GitHub, through the `gh` CLI. Every `gh` invocation fleet makes is here.

    `gh` rather than the REST API directly because it already holds the
    operator's credentials, and because a fleet that needed its own token would
    need one per machine a worker runs on.
    """

    name = "github"
    merge_methods = ("squash", "merge", "rebase")

    def __init__(self, hosts=None):
        # `GH_HOST` is gh's own variable for a GitHub Enterprise instance, so a
        # self-hosted GitHub round-trips through this adapter the same way a
        # self-hosted GitLab will have to through its own.
        extra = [h.strip().lower() for h in (hosts or []) if h and h.strip()]
        enterprise = os.environ.get("GH_HOST", "").strip().lower()
        if enterprise:
            extra.append(enterprise)
        self.hosts = tuple(dict.fromkeys(["github.com", "www.github.com"] + extra))
        # One answer per (repo, login) per process. The question does not
        # change inside a run and every open pull request would ask it again.
        self._push: dict = {}

    # --- naming ---

    def parse_change_url(self, url: str) -> ChangeRef | None:
        m = GH_URL_RE.match((url or "").strip())
        if not m or not self.owns_host(m.group(1)):
            return None
        host = m.group(1).lower()
        return ChangeRef(
            RepoId(host, m.group(2)),
            int(m.group(3)),
            f"https://{host}/{m.group(2)}/pull/{m.group(3)}",
        )

    def repo_from_remote(self, remote_url: str) -> RepoId | None:
        m = GH_REMOTE_RE.match((remote_url or "").strip())
        if not m:
            return None
        host = m.group(1).lower()
        # `git@github.com:owner/repo` has no scheme, so the host is whatever
        # came before the colon; a URL that named none of our hosts is not ours.
        if not self.owns_host(host):
            return None
        return RepoId(host, m.group(2))

    # --- running gh ---

    def _run(self, argv: list, cwd: str | None = None, timeout: int = 60) -> tuple:
        """(stdout, why-not). A non-empty second value is never a verdict."""
        if not shutil.which("gh"):
            return None, "gh not found on PATH"
        try:
            out = subprocess.run(
                ["gh"] + argv, capture_output=True, text=True, cwd=cwd, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"gh could not be run: {exc}"
        if out.returncode != 0:
            detail = ((out.stderr or "") + (out.stdout or "")).strip().splitlines()
            return None, (detail[0] if detail else f"gh exited {out.returncode}")
        return out.stdout, ""

    def _json(self, argv: list, cwd: str | None = None, timeout: int = 60) -> tuple:
        out, why = self._run(argv, cwd=cwd, timeout=timeout)
        if why:
            return None, why
        try:
            return json.loads(out), ""
        except ValueError:
            return None, "gh returned output that is not JSON"

    # --- the questions ---

    def get(self, ref: ChangeRef) -> tuple:
        doc, why = self._json(
            ["pr", "view", ref.url, "--json", GH_ONE_FIELDS], timeout=30
        )
        if why:
            return None, f"gh pr view failed: {why}"
        if not isinstance(doc, dict):
            return None, "gh pr view did not answer with an object"
        doc["url"] = ref.url
        cr = self._change_request(doc, ref.repo)
        if cr is None:
            return None, "gh pr view did not answer about a pull request"
        return cr, ""

    def state(self, ref: ChangeRef) -> tuple:
        out, why = self._run(
            ["pr", "view", ref.url, "--json", "state", "-q", ".state"], timeout=30
        )
        if why:
            return None, f"gh pr view could not read the state: {why}"
        said = (out or "").strip().upper()
        state = GH_STATES.get(said)
        if not state:
            return None, f"gh answered an unrecognised pull request state: {said!r}"
        return state, ""

    def open_change_requests(self, repo: RepoId) -> tuple:
        docs, why = self._json(
            ["pr", "list", "--repo", repo.path, "--state", "open",
             "--limit", str(GH_LIST_LIMIT), "--json", GH_FIELDS]
        )
        return self._listed(docs, why, repo, GH_LIST_LIMIT)

    def open_change_requests_in_checkout(self, path: str) -> tuple:
        docs, why = self._json(
            ["pr", "list", "--state", "open", "--limit", "50",
             "--json", GH_STATUS_FIELDS],
            cwd=path,
            timeout=20,
        )
        return self._listed(docs, why, None, 50)

    def _listed(self, docs, why: str, repo: RepoId | None, limit: int) -> tuple:
        if why:
            return [], why
        if not isinstance(docs, list):
            return [], "gh returned something that is not a list of pull requests"
        docs = [d for d in docs if isinstance(d, dict)]
        if len(docs) >= limit:
            return [], (
                f"the repository has at least {limit} open pull requests; gh's "
                "result may be truncated, so treating it as unreadable rather "
                "than silently dropping some"
            )
        out = []
        for d in docs:
            cr = self._change_request(d, repo)
            if cr:
                out.append(cr)
        return out, ""

    def _change_request(self, d: dict, repo: RepoId | None) -> ChangeRequest | None:
        url = str(d.get("url") or "")
        ref = self.parse_change_url(url)
        if ref is None:
            if repo is None or not d.get("number"):
                return None
            # A repository was asked for by name and gh answered with a pull
            # request whose url it did not give: rebuild it rather than drop it.
            n = int(d["number"])
            ref = ChangeRef(repo, n, url or f"https://{repo.host}/{repo.path}/pull/{n}")

        owner = str((d.get("headRepositoryOwner") or {}).get("login") or "")
        if not owner:
            ours, where = None, ""
        elif d.get("isCrossRepository"):
            ours, where = False, f"{owner}'s fork"
        else:
            ours = owner.lower() == ref.repo.owner.lower()
            where = f"{owner}'s repository"

        author = d.get("author") or {}
        return ChangeRequest(
            ref=ref,
            title=str(d.get("title") or ""),
            state=GH_STATES.get(str(d.get("state") or "").upper(), ""),
            draft=bool(d.get("isDraft")),
            body=str(d.get("body") or ""),
            head_branch=str(d.get("headRefName") or ""),
            base_branch=str(d.get("baseRefName") or ""),
            head_sha=str(d.get("headRefOid") or ""),
            author=str(author.get("login") or ""),
            author_is_bot=bool(author.get("is_bot")),
            mergeable={"MERGEABLE": "mergeable", "CONFLICTING": "conflicting"}.get(
                str(d.get("mergeable") or "").upper(), ""
            ),
            review_decision={"CHANGES_REQUESTED": "changes-requested",
                             "APPROVED": "approved"}.get(
                str(d.get("reviewDecision") or "").upper(), ""
            ),
            checks=self._checks(d.get("statusCheckRollup")),
            commits=[
                Commit(str(c.get("oid") or ""), str(c.get("messageHeadline") or ""))
                for c in (d.get("commits") or [])
                if isinstance(c, dict)
            ],
            head_is_ours=ours,
            head_location=where,
        )

    @staticmethod
    def _checks(rollup) -> list:
        out = []
        for c in rollup or []:
            if not isinstance(c, dict):
                continue
            name = c.get("name") or c.get("context") or "a required check"
            if "state" in c and "conclusion" not in c:
                # A StatusContext: one word, and PENDING is not a failure.
                said = str(c.get("state") or "").upper()
            elif str(c.get("status") or "").upper() != "COMPLETED":
                out.append(Check(name, "pending"))
                continue
            else:
                said = str(c.get("conclusion") or "").upper()
            if said in GH_CHECK_FAILED:
                out.append(Check(name, "failed"))
            elif said in GH_CHECK_PASSED:
                out.append(Check(name, "passed"))
            elif said in GH_CHECK_CANCELLED:
                out.append(Check(name, "cancelled"))
            else:
                out.append(Check(name, "pending"))
        return out

    def can_push(self, repo: RepoId, login: str) -> tuple:
        """Asked as "may this login push here" rather than "is this the owner".

        The owner of `Thurbeen/fleet` is an organisation and no pull request is
        ever authored by one. `gh` has no `authorAssociation` field in every
        version; the collaborator permission endpoint is in all of them.
        """
        key = (repo.qualified, login)
        if key in self._push:
            return self._push[key]
        doc, why = self._json(
            ["api", f"repos/{repo.path}/collaborators/{login}/permission"]
        )
        if why or not isinstance(doc, dict):
            answer = (
                False,
                f"could not check whether {login} can push to {repo}: "
                f"{why or 'unexpected output'}",
            )
        else:
            perm = str(doc.get("permission") or "").lower()
            # GitHub spells "no access" as the literal string `none`.
            said = "no" if perm in ("", "none") else perm
            answer = (perm in GH_PUSH_PERMISSIONS, f"{login} has {said} access to {repo}")
        self._push[key] = answer
        return answer

    def describe_merge(self, method: str, delete_branch: bool) -> str:
        return "gh pr merge --" + method + (" --delete-branch" if delete_branch else "")

    def merge(self, cr: ChangeRequest, method: str, delete_branch: bool) -> tuple:
        if method not in self.merge_methods:
            return False, f"github cannot merge by {method}"
        argv = ["pr", "merge", cr.url, f"--{method}"]
        if delete_branch:
            argv.append("--delete-branch")
        _, why = self._run(argv, timeout=120)
        if why:
            return False, why
        return True, f"{method}-merged" + (", branch deleted" if delete_branch else "")


# --- GitLab, the second implementation ----------------------------------------


# GitLab pages at 100 and no higher, so "every open merge request" is a loop
# rather than one request. The cap is the same promise `GH_LIST_LIMIT` makes:
# reaching it means the project genuinely has that many open merge requests,
# which the caller then treats as unreadable rather than as a short list.
GL_PAGE = 100
GL_LIST_LIMIT = 1000

# What `fleet-status.sh` reads out of a checkout. Lower than the shepherd's cap
# because it decides nothing and one line per merge request is all it prints.
GL_CHECKOUT_LIMIT = 50

# GitLab's own state words. `locked` is a real fourth state and is NOT one of
# fleet's three, so it falls out of this map and is reported as a sentence.
GL_STATES = {"opened": "open", "merged": "merged", "closed": "closed"}

# `head_pipeline.status`. `manual` and `scheduled` are pipelines waiting for
# somebody, which is pending and not passing; `canceled` (GitLab spells it with
# one `l`) is its own verdict for the reason `Check` gives.
GL_PIPELINE_PASSED = {"success", "skipped"}
GL_PIPELINE_FAILED = {"failed"}
GL_PIPELINE_CANCELLED = {"canceled", "cancelling", "canceling"}

# `detailed_merge_status`, of which GitLab has a long and growing list — the
# capture this adapter was written against answered `title_regex`, which is in
# no version of the documented set this code was checked against. So only the
# two words that mean something definite are read, and everything else is "the
# forge has not said", which `classify` treats as ask-again-shortly. Reading an
# unknown word as mergeable is how fleet would merge something GitLab is still
# thinking about.
GL_MERGEABLE = "mergeable"
GL_CONFLICT = "conflict"

# A reviewer pressed "request changes". The one review verdict fleet acts on.
GL_CHANGES_REQUESTED = "requested_changes"

# Developer. GitLab's ladder is 0 none / 5 minimal / 10 guest / 20 reporter /
# 30 developer / 40 maintainer / 50 owner, and developer is the first rung that
# may push.
GL_PUSH_ACCESS_LEVEL = 30

# `squash_option: never` is the setting that forbids fleet's merge method, and
# it is a PROJECT setting rather than a forge one — see `merge` below.
GL_SQUASH_FORBIDDEN = "never"

# A merge request URL: `https://host/group/sub/project/-/merge_requests/12`.
# The host group keeps a `:port`, because a self-hosted instance on one is
# ordinary and `RepoId` carries the port as part of the host.
GL_URL_RE = re.compile(
    r"^https?://([^/\s]+)/(.+?)/-/merge_requests/(\d+)(?:[/?#].*)?$"
)
# `https://host/group/proj.git`, `ssh://git@host:2222/group/proj.git`.
GL_REMOTE_URL_RE = re.compile(
    r"^(?:https?|ssh|git)://(?:[^@/\s]+@)?([^/\s]+)/(.+?)(?:\.git)?/?$"
)
# `git@host:group/proj.git` — scp syntax, which carries no port.
GL_REMOTE_SCP_RE = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):(.+?)(?:\.git)?/?$")

# GitLab reserves these username prefixes for project and group access tokens,
# so they are the one thing in a merge request author that says "not a person".
GL_BOT_RE = re.compile(r"^(?:project|group)_\d+_bot")

# glab prints its own errors as a decorated block on stderr. These are the
# decoration, not the message.
GL_NOISE = {"", "error", "warning"}


class GitLabForge(Forge):
    """GitLab, through the `glab` CLI. Every `glab` invocation fleet makes is here.

    `glab` for the same reason the GitHub adapter uses `gh`: it already holds
    whatever credential the operator gave this machine, and a fleet that needed
    its own token would need one per machine a worker runs on.

    WHICH HOSTS ARE GITLAB. `gitlab.com`, plus whichever instances this
    machine's `glab` is configured for — `configured_hosts` above reads them
    out of `glab auth status`. `GITLAB_HOST`, glab's own variable for one
    chosen instance, overrides that entirely when it is set, the way `GH_HOST`
    does for gh. A self-hosted instance is the normal case here, so every call
    names its repository by FULL URL (`-R https://host/group/project`) rather
    than by slug: that is what makes `gitlab.example.com/group/proj` reach
    gitlab.example.com and not gitlab.com.

    WHAT IT COSTS. GitLab does not put a merge request's pipeline in the list
    endpoint, so listing open change requests is one call for the list plus one
    per merge request. The GitHub adapter gets its whole answer in one call;
    this one cannot, and paying the difference is better than reporting `checks`
    empty, which every caller reads as "no check has reported yet".
    """

    name = "gitlab"
    # GitLab's `squash` is not a merge method: it is a flag ON the merge, and
    # the merge method (`merge` / `rebase_merge` / `ff`) is a separate project
    # setting. So all three of fleet's words are things this forge can do, and
    # the thing that can forbid a squash is per-PROJECT — `merge` asks.
    merge_methods = ("squash", "merge", "rebase")

    def __init__(self, hosts=None):
        extra = [self._host(h) for h in (hosts or [])]
        override = self._host(os.environ.get("GITLAB_HOST", ""))
        # `GITLAB_HOST` DECIDES when it is set, and discovery does not run at
        # all then: it is glab's own variable, an operator who exported it
        # pointed fleet at that instance deliberately, and glab itself obeys it
        # over its config. Unset, this used to mean "gitlab.com and nothing
        # else", which made a self-hosted instance invisible on the very
        # machines whose `glab` was logged in to one — so ask glab which
        # instances it holds instead of waiting for a variable nothing sets.
        extra.extend([override] if override else configured_hosts("glab"))
        self.hosts = tuple(dict.fromkeys(
            ["gitlab.com", "www.gitlab.com"] + [h for h in extra if h]
        ))
        # One answer per (repo, login), and one per repo for the squash
        # setting: neither changes inside a run, and every open merge request
        # would otherwise ask again.
        self._push: dict = {}
        self._squash: dict = {}

    @staticmethod
    def _host(text: str) -> str:
        """`https://gitlab.example.com/` as glab accepts it, down to a bare host."""
        text = str(text or "").strip().lower()
        text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)
        return text.strip("/").split("/")[0]

    # --- naming ---

    def parse_change_url(self, url: str) -> ChangeRef | None:
        m = GL_URL_RE.match((url or "").strip())
        if not m or not self.owns_host(m.group(1)):
            return None
        host, path = m.group(1).lower(), m.group(2)
        # `group/project` at the very least: GitLab has no top-level projects,
        # so a single segment is not a project path and not ours.
        if "/" not in path:
            return None
        return ChangeRef(
            RepoId(host, path),
            int(m.group(3)),
            f"https://{host}/{path}/-/merge_requests/{m.group(3)}",
        )

    def repo_from_remote(self, remote_url: str) -> RepoId | None:
        text = (remote_url or "").strip()
        m = GL_REMOTE_URL_RE.match(text) or GL_REMOTE_SCP_RE.match(text)
        if not m:
            return None
        host, path = m.group(1).lower(), m.group(2).strip("/")
        if not self.owns_host(host) or "/" not in path:
            return None
        return RepoId(host, path)

    # --- running glab ---

    def _repo_arg(self, repo: RepoId) -> str:
        """How glab is told WHICH host, on every single call.

        `-R` takes a full URL as readily as a slug, and the URL is the only
        form that carries the host — so this is what keeps a self-hosted
        instance from being asked of gitlab.com. https because a GitLab
        instance reachable only over plain http cannot be named this way; that
        is the one shape of self-hosted install this adapter cannot address.
        """
        return f"https://{repo.host}/{repo.path}"

    def _run(self, argv: list, cwd: str | None = None, timeout: int = 60) -> tuple:
        """(stdout, why-not). A non-empty second value is never a verdict."""
        if not shutil.which("glab"):
            return None, "glab not found on PATH"
        try:
            out = subprocess.run(
                ["glab"] + argv, capture_output=True, text=True, cwd=cwd, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"glab could not be run: {exc}"
        if out.returncode != 0:
            return None, self._why(out.stdout, out.stderr, out.returncode)
        return out.stdout, ""

    @staticmethod
    def _why(stdout: str, stderr: str, code: int) -> str:
        """The sentence glab actually said, out of the two places it says it.

        With `-F json` glab puts `{"error":{"message":...}}` on STDOUT and a
        boxed, blank-line-padded `ERROR` block on stderr, so taking the first
        line of stderr yields the box and not the reason. `glab api` puts the
        API's own `{"message":...}` on stdout instead. Both are read before
        stderr is fallen back to.
        """
        try:
            doc = json.loads(stdout or "")
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            err = doc.get("error")
            said = err.get("message") if isinstance(err, dict) else doc.get("message")
            if isinstance(said, str) and said.strip():
                return said.strip()
        for line in (stderr or "").splitlines():
            line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if line.lower().rstrip(":") not in GL_NOISE:
                return line
        return f"glab exited {code}"

    def _json(self, argv: list, cwd: str | None = None, timeout: int = 60) -> tuple:
        out, why = self._run(argv, cwd=cwd, timeout=timeout)
        if why:
            return None, why
        try:
            return json.loads(out), ""
        except ValueError:
            return None, "glab returned output that is not JSON"

    def _api(self, repo: RepoId, path: str, timeout: int = 60) -> tuple:
        """`glab api` against ONE host, for the questions `glab mr` has no verb for."""
        return self._json(["api", path, "--hostname", repo.host], timeout=timeout)

    @staticmethod
    def _project(repo: RepoId) -> str:
        """`group%2Fsub%2Fproject` — how a project path goes into an API path."""
        return urllib.parse.quote(repo.path, safe="")

    def _view(self, repo: RepoId, number: int) -> tuple:
        out, why = self._json(
            ["mr", "view", str(number), "-R", self._repo_arg(repo), "-F", "json"],
            timeout=30,
        )
        if why:
            return None, f"glab mr view failed: {why}"
        if not isinstance(out, dict):
            return None, "glab mr view did not answer with an object"
        return out, ""

    # --- the questions ---

    def get(self, ref: ChangeRef) -> tuple:
        doc, why = self._view(ref.repo, ref.number)
        if why:
            return None, why
        return self._change_request(doc, ref.repo, self._commits(ref)), ""

    def _commits(self, ref: ChangeRef) -> list:
        """Oldest first, which is the opposite of the order GitLab answers in.

        Read for one thing — telling the pipeline's own follow-up push apart
        from somebody pushing over it — so a call that fails answers with an
        empty list rather than a guess, exactly as `Commit` says it must.
        """
        docs, _why = self._api(
            ref.repo,
            f"projects/{self._project(ref.repo)}/merge_requests/{ref.number}"
            f"/commits?per_page={GL_PAGE}",
            timeout=30,
        )
        if not isinstance(docs, list):
            return []
        out = [
            Commit(str(c.get("id") or ""), str(c.get("title") or ""))
            for c in docs
            if isinstance(c, dict)
        ]
        out.reverse()
        return out

    def state(self, ref: ChangeRef) -> tuple:
        out, why = self._run(
            ["mr", "view", str(ref.number), "-R", self._repo_arg(ref.repo),
             "-F", "json", "--jq", ".state"],
            timeout=30,
        )
        if why:
            return None, f"glab mr view could not read the state: {why}"
        said = (out or "").strip().strip('"').lower()
        state = GL_STATES.get(said)
        if not state:
            return None, f"glab answered an unrecognised merge request state: {said!r}"
        return state, ""

    def open_change_requests(self, repo: RepoId) -> tuple:
        docs, why = self._page(repo, GL_LIST_LIMIT)
        if why:
            return [], why
        return self._enriched(docs, repo)

    def open_change_requests_in_checkout(self, path: str) -> tuple:
        docs, why = self._json(
            ["mr", "list", "-F", "json", "--per-page", str(GL_CHECKOUT_LIMIT)],
            cwd=path,
            timeout=20,
        )
        if why:
            return [], why
        if not isinstance(docs, list):
            return [], "glab returned something that is not a list of merge requests"
        docs = [d for d in docs if isinstance(d, dict)]
        if len(docs) >= GL_CHECKOUT_LIMIT:
            return [], self._truncated(GL_CHECKOUT_LIMIT)
        # A checkout names no repository, so take the one every merge request
        # already carries: its own web_url. A directory whose merge requests
        # are on a host this adapter does not own is not ours to answer for.
        repo = None
        for d in docs:
            ref = self.parse_change_url(str(d.get("web_url") or ""))
            if ref is None:
                return [], (
                    "glab answered with a merge request whose web_url is on no "
                    "host this adapter owns"
                )
            repo = ref.repo
        if repo is None:
            return [], "" if self._is_ours(path) else "not a checkout of a GitLab project"
        return self._enriched(docs, repo)

    def _is_ours(self, path: str) -> bool:
        """Does this checkout's `origin` name a host we own? Only asked when it
        has no open merge request to answer with, since an empty list has to be
        "none are open" and not "this is a GitHub repository"."""
        return self.repo_from_remote(_git_remote(path)) is not None

    def _page(self, repo: RepoId, limit: int) -> tuple:
        """Every open merge request, one page of 100 at a time."""
        out: list = []
        page = 1
        while len(out) < limit:
            docs, why = self._json(
                ["mr", "list", "-R", self._repo_arg(repo), "-F", "json",
                 "--per-page", str(GL_PAGE), "--page", str(page)]
            )
            if why:
                return [], why
            if not isinstance(docs, list):
                return [], "glab returned something that is not a list of merge requests"
            docs = [d for d in docs if isinstance(d, dict)]
            out.extend(docs)
            if len(docs) < GL_PAGE:
                return out, ""
            page += 1
        return [], self._truncated(limit)

    @staticmethod
    def _truncated(limit: int) -> str:
        return (
            f"the project has at least {limit} open merge requests; glab's result "
            "may be truncated, so treating it as unreadable rather than silently "
            "dropping some"
        )

    def _enriched(self, docs: list, repo: RepoId) -> tuple:
        """The list, with the pipeline GitLab leaves out of it.

        One failure fails the WHOLE list. A short list reads as "these are all
        the open merge requests", and dropping the conflicting one from it is
        how a shepherd would decide it had nothing to report.
        """
        out = []
        for d in docs:
            number = d.get("iid")
            if not number:
                continue
            full, why = self._view(repo, int(number))
            if why:
                return [], f"could not read merge request !{number} on {repo}: {why}"
            out.append(self._change_request(full, repo))
        return out, ""

    def _change_request(self, d: dict, repo: RepoId, commits=None) -> ChangeRequest:
        number = int(d.get("iid") or 0)
        ref = self.parse_change_url(str(d.get("web_url") or "")) or ChangeRef(
            repo, number, f"https://{repo.host}/{repo.path}/-/merge_requests/{number}"
        )

        # WHOSE BRANCH. On GitLab a fork is a project of its own, so this is
        # two integers and not a name — and when either is missing the answer
        # is `None`, which `classify` reads as undetermined rather than as a
        # stranger or as one of ours.
        source, target = d.get("source_project_id"), d.get("target_project_id")
        if not isinstance(source, int) or not isinstance(target, int):
            ours, where = None, ""
        elif source == target:
            ours, where = True, f"{ref.repo.path} itself"
        else:
            # The merge request says which project the branch is in by id and
            # never by name, and resolving the id would be another call for a
            # sentence nobody acts on.
            ours, where = False, f"another project on {ref.repo.host} (id {source})"

        said = str(d.get("detailed_merge_status") or "").lower()
        if d.get("has_conflicts") is True or said == GL_CONFLICT:
            mergeable = "conflicting"
        elif said == GL_MERGEABLE:
            mergeable = "mergeable"
        else:
            mergeable = ""

        author = d.get("author") or {}
        login = str(author.get("username") or "")
        return ChangeRequest(
            ref=ref,
            title=str(d.get("title") or ""),
            state=GL_STATES.get(str(d.get("state") or "").lower(), ""),
            draft=bool(d.get("draft")),
            body=str(d.get("description") or ""),
            head_branch=str(d.get("source_branch") or ""),
            base_branch=str(d.get("target_branch") or ""),
            head_sha=str(d.get("sha") or ""),
            author=login,
            # The author object carries no `bot` flag, so the only thing that
            # says "not a person" is the username shape GitLab reserves for
            # project and group access tokens.
            author_is_bot=bool(GL_BOT_RE.match(login)),
            mergeable=mergeable,
            review_decision=(
                "changes-requested" if said == GL_CHANGES_REQUESTED else ""
            ),
            checks=self._checks(d),
            commits=list(commits or []),
            head_is_ours=ours,
            head_location=where,
        )

    @staticmethod
    def _checks(d: dict) -> list:
        """The head pipeline, as one check — and NOTHING when it is not the head's.

        GitLab keeps the previous commit's pipeline in `head_pipeline` until the
        new one is created, so a pipeline whose `sha` is not the merge request's
        is a green light for code nobody ran. An empty list is `classify`'s "no
        check has reported yet", which is the correct answer there.
        """
        p = d.get("head_pipeline")
        if not isinstance(p, dict):
            return []
        ran, head = str(p.get("sha") or ""), str(d.get("sha") or "")
        if ran and head and ran.lower() != head.lower():
            return []
        name = str(p.get("name") or "") or f"pipeline #{p.get('id') or 'unnumbered'}"
        said = str(p.get("status") or "").lower()
        if said in GL_PIPELINE_FAILED:
            return [Check(name, "failed")]
        if said in GL_PIPELINE_PASSED:
            return [Check(name, "passed")]
        if said in GL_PIPELINE_CANCELLED:
            return [Check(name, "cancelled")]
        return [Check(name, "pending")]

    def can_push(self, repo: RepoId, login: str) -> tuple:
        """May this account push here — asked of the members list, by username.

        `members/all` rather than `members`: it includes membership inherited
        from the group, which is how almost everybody who can push to a GitLab
        project has it.
        """
        key = (repo.qualified, login)
        if key in self._push:
            return self._push[key]
        docs, why = self._api(
            repo,
            f"projects/{self._project(repo)}/members/all"
            f"?query={urllib.parse.quote(login)}&per_page={GL_PAGE}",
        )
        if why or not isinstance(docs, list):
            answer = (
                False,
                f"could not check whether {login} can push to {repo}: "
                f"{why or 'unexpected output'}",
            )
        else:
            level = None
            for m in docs:
                if not isinstance(m, dict):
                    continue
                if str(m.get("username") or "").lower() == login.lower():
                    level = m.get("access_level")
                    break
            if not isinstance(level, int):
                answer = (False, f"{login} is not a member of {repo}")
            else:
                answer = (
                    level >= GL_PUSH_ACCESS_LEVEL,
                    f"{login} has access level {level} on {repo}, and "
                    f"{GL_PUSH_ACCESS_LEVEL} (developer) is the first that may push",
                )
        self._push[key] = answer
        return answer

    def _squash_allowed(self, repo: RepoId) -> tuple:
        """(True / False / None, why). `None` is "the project did not say".

        A project can be configured `squash_option: never`, and squash is the
        only method fleet merges by. Asking first turns that into a refusal
        fleet records, rather than an API error after the fact — and `None`
        must not block, because "I could not read the setting" is not "the
        setting forbids it".
        """
        if repo.qualified in self._squash:
            return self._squash[repo.qualified]
        doc, why = self._api(repo, f"projects/{self._project(repo)}")
        if why or not isinstance(doc, dict):
            answer = (None, why or "glab did not answer with a project")
        elif str(doc.get("squash_option") or "").lower() == GL_SQUASH_FORBIDDEN:
            answer = (False, (
                f"{repo} is configured `squash_option: never`, and squash is the "
                "only method fleet merges by"
            ))
        else:
            answer = (True, "")
        self._squash[repo.qualified] = answer
        return answer

    def describe_merge(self, method: str, delete_branch: bool) -> str:
        argv = ["glab mr merge --yes --auto-merge=false"]
        if method == "squash":
            argv.append("--squash")
        elif method == "rebase":
            argv.append("--rebase")
        if delete_branch:
            argv.append("--remove-source-branch")
        return " ".join(argv)

    def merge(self, cr: ChangeRequest, method: str, delete_branch: bool) -> tuple:
        if method not in self.merge_methods:
            return False, f"gitlab cannot merge by {method}"
        if method == "squash":
            allowed, why = self._squash_allowed(cr.repo)
            if allowed is False:
                return False, why
        argv = ["mr", "merge", str(cr.number), "-R", self._repo_arg(cr.repo),
                "--yes", "--auto-merge=false"]
        if method == "squash":
            argv.append("--squash")
        elif method == "rebase":
            argv.append("--rebase")
        if delete_branch:
            argv.append("--remove-source-branch")
        if cr.head_sha:
            # Merge THIS commit or nothing. glab's own flag for it, and the
            # only thing standing between "fleet checked the head" and a push
            # that lands between the check and the merge.
            argv += ["--sha", cr.head_sha]
        _, why = self._run(argv, timeout=120)
        if why:
            return False, why
        return True, f"{method}-merged" + (
            ", source branch removed" if delete_branch else ""
        )


# --- the registry ------------------------------------------------------------


BUILTIN = (GitHubForge, GitLabForge)

# A colon-separated list of Python files, each exporting `forges()`. This is
# how the selftest drives the whole queue through a forge that has no network
# and no `gh` behind it, and how a forge fleet does not ship can be tried out
# without editing this file.
PLUGIN_ENV = "FLEET_FORGE_PLUGINS"

_REGISTRY: list | None = None

# So a plugin file can `import fleet_forge` and get THIS module whichever name
# it was loaded under — `forge` off sys.path, `fleet_forge` through queue.py's
# importlib loader. One module object means one registry.
sys.modules.setdefault("fleet_forge", sys.modules[__name__])


def forges() -> list:
    """Every configured forge, built-ins first. Cached for the process."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = [cls() for cls in BUILTIN]
        for path in os.environ.get(PLUGIN_ENV, "").split(":"):
            path = path.strip()
            if path:
                _REGISTRY.extend(_load_plugin(path))
    return _REGISTRY


def reset() -> None:
    """Forget the cached registry. For tests inside one process."""
    global _REGISTRY
    _REGISTRY = None


def _load_plugin(path: str) -> list:
    """Import one plugin file and take the forges it exports.

    A plugin that cannot be loaded is reported on stderr and skipped, never
    raised: a bad entry in an environment variable must not take down a
    shepherd pass that had nothing to do with it.
    """
    try:
        spec = importlib.util.spec_from_file_location(
            "fleet_forge_plugin_" + re.sub(r"\W", "_", path), path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"{path} is not an importable Python file")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        got = mod.forges()
        return [f for f in got if isinstance(f, Forge)]
    except Exception as exc:  # noqa: BLE001 - a plugin may fail any way it likes
        print(f"forge: could not load {PLUGIN_ENV} entry {path}: {exc}", file=sys.stderr)
        return []


def for_url(url: str) -> tuple:
    """(forge, ref) for a change request URL, or (None, why-not).

    "No configured forge owns that host" is a REASON and not a verdict: an
    artifact on a forge nobody configured is one fleet could not ask about,
    which is exactly what `collect` and the landing check call `unknown`.
    """
    canonical = change_url(url)
    if not canonical:
        return None, "not a change request URL"
    for f in forges():
        ref = f.parse_change_url(canonical)
        if ref is not None:
            return f, ref
    host = canonical.split("/")[2] if "://" in canonical else canonical
    return None, f"no configured forge owns {host}"


def for_repo(repo: RepoId) -> tuple:
    """(forge, '') for a repository, or (None, why-not)."""
    for f in forges():
        if f.owns_host(repo.host):
            return f, ""
    return None, f"no configured forge owns {repo.host}"


def repo_from_remote(remote_url: str) -> RepoId | None:
    """The repository a git remote URL names, asked of every configured forge."""
    for f in forges():
        repo = f.repo_from_remote(remote_url)
        if repo is not None:
            return repo
    return None


def open_change_requests_in_checkout(path: str) -> tuple:
    """Every open change request in a local checkout, without naming its repository.

    The one question asked of a PATH rather than of a `RepoId`, because
    `fleet-status.sh` has a checkout on disk and no identity for it — and a
    directory that is not a git repository at all still has to produce a
    sentence rather than an empty list that reads as "nothing is open".
    """
    forge = None
    remote = _git_remote(path)
    if remote:
        for f in forges():
            if f.repo_from_remote(remote) is not None:
                forge = f
                break
    candidates = [forge] if forge else list(forges())
    reasons = []
    for f in candidates:
        crs, err = f.open_change_requests_in_checkout(path)
        if not err:
            return crs, ""
        reasons.append(err)
    # EVERY reason, not the last one. Once two forges are configured, the
    # commonest failure here is that neither CLI is installed, and reporting
    # only whichever was tried second names one missing tool and hides the
    # other — which reads as "install glab" on a machine that talks to GitHub.
    return [], "; ".join(dict.fromkeys(reasons)) or "no forge is configured"


def _git_remote(path: str) -> str:
    if not path or not os.path.isdir(path):
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


# --- asking from a shell -----------------------------------------------------


if __name__ == "__main__":
    # `python3 scripts/lib/forge.py hosts glab` — the one thing in this module
    # a shell script needs, since `configured_hosts` answers a question
    # (`scripts/preflight.sh`'s "which instances should I report auth for")
    # that is not itself about a change request. One host per line, nothing on
    # a machine that has no such CLI, and always exit 0: a CLI that is not
    # installed is an answer, not an error.
    if len(sys.argv) == 3 and sys.argv[1] == "hosts":
        for _host in configured_hosts(sys.argv[2]):
            print(_host)
    else:
        print("usage: forge.py hosts <gh|glab>", file=sys.stderr)
        sys.exit(2)
