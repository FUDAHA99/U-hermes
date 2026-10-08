# -*- coding: utf-8 -*-
"""Run the bot's real shell step against real git, one scenario at a time.

The step in .github/workflows/update-pins.yml that pushes and opens the pull
request decides, from git and from GitHub, whether it may touch the proposal
branch. Two rounds of review found ways that decision went wrong: a person's
amend force-pushed away (the check read only the author), a stranger's fork PR
edited into the proposal, the bot going quiet for good once a person had
committed, a declined offer coming back (also through an API error read as
"not declined"), a stranger's issue edited under the bot's text, and a run
that failed after its push leaving the PR stale and unbuilt for good.
Reading the YAML cannot show any of that, so this extracts the step's script
and runs it: a bare repository stands in for GitHub's, a fake `gh` holds the
pull requests, issues and runs and records every call, and the step's issue
helpers are the real tools/update_pins.py. Where a real `jq` exists (GitHub's
runners), the fake answers the step's --jq filters with it, so the filters
themselves are tested; elsewhere it knows exactly those filters and refuses
any other.

Run:  python tools/tests/test_update_pins_workflow.py
"""
import io
import json
import os
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
WORKFLOW = os.path.join(REPO, ".github", "workflows", "update-pins.yml")
SCRIPT = os.path.join(TOOLS, "update_pins.py")
TMP = os.path.join(HERE, "_tmp_update_pins_wf")
STEP = "- name: Open or refresh the pull request"

BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
BRANCH = "auto/update-pins"
ISSUE_TITLE = "[自动] 上游有新版本，等你开 PR"
NL = chr(10)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


def rmtree(path):
    """git writes its objects read-only; on Windows plain rmtree leaves them,
    and with ignore_errors the next run then finds a half-deleted world."""
    import stat

    def unlock(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if os.path.exists(path):
        # onexc replaced onerror in 3.12; same (func, path, exc) shape.
        hook = "onexc" if sys.version_info >= (3, 12) else "onerror"
        shutil.rmtree(path, **{hook: unlock})


def find_bash():
    """Git's bash. On Windows, `bash` on PATH can be WSL's, which has neither
    this filesystem layout nor our git."""
    if os.name == "nt":
        for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if os.path.exists(p):
                return p
        return None
    return shutil.which("bash")


def step_script():
    lines = io.open(WORKFLOW, encoding="utf-8").read().split(NL)
    start = next(i for i, l in enumerate(lines) if STEP in l)
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = None
    body = []
    for l in lines[run + 1:]:
        if l.strip() and indent is None:
            indent = len(l) - len(l.lstrip())
        if l.strip() and len(l) - len(l.lstrip()) < indent:
            break
        body.append(l[indent:] if l.strip() else "")
    return NL.join(body) + NL


FAKE_GH = r'''
import json, os, shutil, subprocess, sys
# The real gh writes UTF-8. A Windows pipe would otherwise get the ANSI code
# page, and every Chinese title would stop matching $TITLE -- which once made
# the declined check look broken when only this fake was.
sys.stdout.reconfigure(encoding="utf-8")
state_dir = os.environ["FAKE_GH_DIR"]
def load(name, default):
    p = os.path.join(state_dir, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default
def save(name, data):
    json.dump(data, open(os.path.join(state_dir, name), "w", encoding="utf-8"), ensure_ascii=False)
def log(entry):
    with open(os.path.join(state_dir, "calls.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default
KNOWN = {
    '[.[] | select(.isCrossRepository | not)] | .[] | select(.mergedAt == null) | .title':
        lambda rows: [r["title"] for r in rows if not r["isCrossRepository"] and r.get("mergedAt") is None],
    '[.[] | select(.isCrossRepository | not)] | .[].number':
        lambda rows: [str(r["number"]) for r in rows if not r["isCrossRepository"]],
    '[.[] | select(.isCrossRepository | not)] | .[].headRefOid':
        lambda rows: [r["headRefOid"] for r in rows if not r["isCrossRepository"]],
    '.[].headSha': lambda rows: [r["headSha"] for r in rows],
}
def answer(rows, fields, expr):
    rows = [{k: r.get(k) for k in fields} for r in rows]
    if expr is None:
        print(json.dumps(rows, ensure_ascii=False)); return
    if shutil.which("jq"):
        out = subprocess.run(["jq", "-r", expr], input=json.dumps(rows, ensure_ascii=False),
                             capture_output=True, text=True, encoding="utf-8")
        if out.returncode != 0:
            sys.stderr.write("jq rejected the filter: " + out.stderr); sys.exit(3)
        sys.stdout.write(out.stdout); return
    if expr not in KNOWN:
        sys.stderr.write("fake gh: unknown --jq filter, teach the test: " + expr + "\n"); sys.exit(3)
    for line in KNOWN[expr](rows):
        print(line)
def state_ok(item, state):
    return state == "all" or (state == "open" and item["state"] == "OPEN") or \
        (state == "closed" and item["state"] in ("CLOSED", "MERGED"))
args = sys.argv[1:]
log({"argv": args})
fail_on = os.environ.get("FAKE_GH_FAIL_ON")
if fail_on and fail_on in " ".join(args):
    sys.stderr.write("HTTP 502: Bad Gateway\n"); sys.exit(1)
prs, issues, runs = load("prs.json", []), load("issues.json", []), load("runs.json", [])
if args[:2] == ["pr", "list"]:
    pick = [p for p in prs if p["headRefName"] == opt("--head") and state_ok(p, opt("--state", "open"))]
    answer(pick, opt("--json").split(","), opt("--jq"))
elif args[:2] == ["pr", "create"]:
    if os.path.exists(os.path.join(state_dir, "pr_create_fails")):
        sys.stderr.write("GitHub Actions is not permitted to create or approve pull requests.\n"); sys.exit(1)
    prs.append({"number": 100 + len(prs), "state": "OPEN", "isCrossRepository": False,
                "headRefName": opt("--head"), "title": opt("--title"), "mergedAt": None, "headRefOid": ""})
    save("prs.json", prs)
elif args[:2] == ["pr", "edit"]:
    for p in prs:
        if str(p["number"]) == args[2]:
            p["title"] = opt("--title")
    save("prs.json", prs)
elif args[:2] == ["repo", "view"]:
    print("false" if os.path.exists(os.path.join(state_dir, "issues_disabled")) else "true")
elif args[:2] == ["issue", "list"]:
    if os.path.exists(os.path.join(state_dir, "issues_disabled")):
        sys.stderr.write("the 'FUDAHA99/U-hermes' repository has disabled issues\n"); sys.exit(1)
    # As real gh 2.95 behaves: --app becomes an author filter GitHub matches
    # to nothing; --author 'github-actions[bot]' is what finds the bot's issues.
    if opt("--app") is not None:
        pick = []
    else:
        who = opt("--author")
        pick = [i for i in issues if state_ok(i, opt("--state", "open")) and
                (who is None or (who == "github-actions[bot]" and i.get("app") == "github-actions"))]
    answer(pick, opt("--json").split(","), opt("--jq"))
elif args[:2] == ["issue", "create"]:
    if os.path.exists(os.path.join(state_dir, "issues_disabled")):
        sys.stderr.write("the 'FUDAHA99/U-hermes' repository has disabled issues\n"); sys.exit(1)
    issues.append({"number": 200 + len(issues), "state": "OPEN", "title": opt("--title"),
                   "body": open(opt("--body-file"), encoding="utf-8").read(),
                   "app": "github-actions", "author": {"is_bot": True, "login": "app/github-actions"}})
    save("issues.json", issues)
elif args[:2] == ["issue", "edit"]:
    for i in issues:
        if str(i["number"]) == args[2]:
            i["body"] = open(opt("--body-file"), encoding="utf-8").read()
    save("issues.json", issues)
elif args[:2] == ["run", "list"]:
    answer([r for r in runs if r.get("branch") == opt("--branch")], opt("--json").split(","), opt("--jq"))
elif args[:2] == ["workflow", "run"]:
    pass
else:
    sys.stderr.write("fake gh: unexpected call %r\n" % args); sys.exit(2)
'''


def git(cwd, *args, **kw):
    env = dict(os.environ, GIT_AUTHOR_DATE="2026-10-08T00:00:00", GIT_COMMITTER_DATE="2026-10-08T00:00:00")
    env.update(kw.get("env", {}))
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, env=env)
    if out.returncode != 0 and not kw.get("ok_to_fail"):
        raise RuntimeError("git %s: %s" % (" ".join(args), out.stderr))
    return out.stdout.strip()


def who(name, email):
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


BOT = who("github-actions[bot]", BOT_EMAIL)
PERSON = who("Felix", "felix@example.com")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def pins(web_ui):
    return "UV_VERSION=0.12.16" + NL + "HERMES_WEB_UI_VERSION=%s" % web_ui + NL


class World(object):
    """origin (bare), a helper clone to shape the branch, and the runner's clone."""

    def __init__(self, name):
        self.root = os.path.join(TMP, name)
        rmtree(self.root)
        self.origin = os.path.join(self.root, "origin.git")
        self.helper = os.path.join(self.root, "helper")
        self.runner = os.path.join(self.root, "runner")
        self.state = os.path.join(self.root, "gh")
        self.temp = os.path.join(self.root, "runner_temp")
        for d in (self.state, self.temp, os.path.join(self.root, "bin")):
            os.makedirs(d)
        git(self.root, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.root, "clone", "-q", "-c", "core.autocrlf=false", self.origin, self.helper)
        write(os.path.join(self.helper, "portable", "versions.env"), pins("0.7.24"))
        # The step calls the real issue helpers in tools/update_pins.py.
        write(os.path.join(self.helper, "tools", "update_pins.py"), io.open(SCRIPT, encoding="utf-8").read())
        git(self.helper, "add", "-A")
        git(self.helper, "commit", "-q", "-m", "main", env=PERSON)
        git(self.helper, "push", "-q", "origin", "HEAD:main")
        self.prs, self.issues, self.runs = [], [], []

    def branch_commit(self, path, text, ident, from_main=True):
        """Shape origin's auto/update-pins as the bot or a person would."""
        if from_main:
            git(self.helper, "checkout", "-q", "-B", BRANCH, "origin/main")
        write(os.path.join(self.helper, path), text)
        git(self.helper, "add", "-A")
        git(self.helper, "commit", "-q", "-m", "change", env=ident)
        git(self.helper, "push", "-q", "-f", "origin", "HEAD:refs/heads/" + BRANCH)
        return git(self.helper, "rev-parse", "HEAD")

    def amend_as_person(self, path, text):
        """`git commit --amend` keeps the bot as author; only the committer changes."""
        write(os.path.join(self.helper, path), text)
        git(self.helper, "add", "-A")
        git(self.helper, "commit", "-q", "--amend", "--no-edit", env={
            "GIT_COMMITTER_NAME": "Felix", "GIT_COMMITTER_EMAIL": "felix@example.com"})
        git(self.helper, "push", "-q", "-f", "origin", "HEAD:refs/heads/" + BRANCH)
        return git(self.helper, "rev-parse", "HEAD")

    def tip(self):
        return git(self.root, "--git-dir", self.origin, "rev-parse", "--verify", "-q",
                   "refs/heads/" + BRANCH, ok_to_fail=True)

    def proposed(self):
        return git(self.root, "--git-dir", self.origin, "show",
                   BRANCH + ":portable/versions.env", ok_to_fail=True)

    def gh_state(self, name):
        return json.load(io.open(os.path.join(self.state, name), encoding="utf-8"))

    def merge_branch_then_revert(self):
        """The proposal merged with a merge commit, then reverted on main."""
        git(self.helper, "checkout", "-q", "-B", "main", "origin/main")
        git(self.helper, "merge", "-q", "--no-ff", "-m", "Merge PR", "origin/" + BRANCH, env=PERSON)
        write(os.path.join(self.helper, "portable", "versions.env"), pins("0.7.24"))
        git(self.helper, "commit", "-q", "-am", "revert the Web UI pin", env=PERSON)
        git(self.helper, "push", "-q", "origin", "HEAD:main")

    def run(self, bash, title, web_ui, pr_create_fails=False, fail_on=None, issues_disabled=False):
        """What the workflow does: plan wrote versions.env, then this step runs."""
        for name, data in (("prs.json", self.prs), ("issues.json", self.issues), ("runs.json", self.runs)):
            json.dump(data, io.open(os.path.join(self.state, name), "w", encoding="utf-8"), ensure_ascii=False)
        if pr_create_fails:
            io.open(os.path.join(self.state, "pr_create_fails"), "w").close()
        if issues_disabled:
            io.open(os.path.join(self.state, "issues_disabled"), "w").close()
        # A Linux runner's checkout: no CRLF conversion, whatever this machine says.
        git(self.root, "clone", "-q", "-c", "core.autocrlf=false", self.origin, self.runner)
        write(os.path.join(self.runner, "portable", "versions.env"), pins(web_ui))
        write(os.path.join(self.temp, "body.md"), "PR 正文" + NL)
        write(os.path.join(self.temp, "commit.txt"), title + NL)
        fake = os.path.join(self.root, "fake_gh.py")
        write(fake, FAKE_GH)
        gh = os.path.join(self.root, "bin", "gh")
        write(gh, "#!/usr/bin/env bash" + NL + 'exec "%s" "%s" "$@"' % (
            sys.executable.replace("\\", "/"), fake.replace("\\", "/")) + NL)
        os.chmod(gh, 0o755)
        # `python` in the step must be a real interpreter (on Windows the
        # Store alias can shadow it), so put this one first.
        py = os.path.join(self.root, "bin", "python")
        write(py, "#!/usr/bin/env bash" + NL + 'exec "%s" "$@"' % sys.executable.replace("\\", "/") + NL)
        os.chmod(py, 0o755)
        script = os.path.join(self.root, "step.sh")
        write(script, step_script())
        env = dict(os.environ, BRANCH=BRANCH, BOT_NAME="github-actions[bot]", BOT_EMAIL=BOT_EMAIL,
                   TITLE=title, RUNNER_TEMP=self.temp, GITHUB_REPOSITORY="FUDAHA99/U-hermes",
                   GH_TOKEN="x", FAKE_GH_DIR=self.state, PYTHONUTF8="1")
        if fail_on:
            env["FAKE_GH_FAIL_ON"] = fail_on
        # Git's bash builds its PATH from the Windows one; the fakes go first.
        env["PATH"] = os.path.join(self.root, "bin") + os.pathsep + os.environ["PATH"]
        out = subprocess.run([bash, script], cwd=self.runner, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env)
        calls = []
        log = os.path.join(self.state, "calls.jsonl")
        if os.path.exists(log):
            calls = [json.loads(l)["argv"] for l in io.open(log, encoding="utf-8") if l.strip()]
        return out, calls


def tail(out):
    lines = [l for l in (out.stderr or "").strip().split(NL) if l.strip()]
    return lines[-1][-160:] if lines else ""


def did(calls, *prefix):
    return [c for c in calls if c[:len(prefix)] == list(prefix)]


def pr(number, state, title, head_oid="", fork=False, merged=False):
    return {"number": number, "state": state, "title": title, "headRefName": BRANCH,
            "isCrossRepository": fork, "mergedAt": "2026-10-01T00:00:00Z" if merged else None,
            "headRefOid": head_oid}


def issue(number, state, body, by_bot=True, title=ISSUE_TITLE):
    return {"number": number, "state": state, "title": title, "body": body,
            "app": "github-actions" if by_bot else None,
            "author": {"is_bot": by_bot, "login": "app/github-actions" if by_bot else "stranger"}}


T_NEW = "跟进上游版本：网页界面 0.7.31"
T_OLD = "跟进上游版本：网页界面 0.7.30"


def scenarios(bash):
    print("first run: no branch, no PR")
    w = World("first")
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(out.returncode == 0, "the step succeeds (%s)" % tail(out))
    check("HERMES_WEB_UI_VERSION=0.7.31" in w.proposed(), "the proposal branch is pushed with the new pins")
    check(len(did(calls, "pr", "create")) == 1, "a PR is opened")
    check(did(calls, "workflow", "run") == [["workflow", "run", "release.yml", "--ref", BRANCH]],
          "and the build is started on the proposal branch")

    print("upstream moved again; the open PR has only the bot's commit")
    w = World("refresh")
    w.branch_commit("portable/versions.env", pins("0.7.30"), BOT)
    w.prs = [pr(7, "OPEN", T_OLD)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(out.returncode == 0, "the step succeeds (%s)" % tail(out))
    check("HERMES_WEB_UI_VERSION=0.7.31" in w.proposed(), "the branch is refreshed to the new pins")
    check(did(calls, "pr", "edit", "7"), "the existing PR is edited, not a second one opened")
    check(not did(calls, "pr", "create"), "...no new PR")
    check(did(calls, "workflow", "run"), "...and the new commit is built")

    print("same versions already proposed, PR up to date, already built")
    w = World("same")
    tip = w.branch_commit("portable/versions.env", pins("0.7.31"), BOT)
    w.prs = [pr(7, "OPEN", T_NEW)]
    w.runs = [{"branch": BRANCH, "headSha": tip}]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(out.returncode == 0 and w.tip() == tip, "nothing is pushed")
    check(not did(calls, "workflow", "run"), "no second build of a commit that was built")

    print("a run that failed after its push: the PR still has last week's text, nothing built")
    w = World("retry")
    tip = w.branch_commit("portable/versions.env", pins("0.7.31"), BOT)
    w.prs = [pr(7, "OPEN", T_OLD)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(out.returncode == 0 and w.tip() == tip, "the branch is not pushed again")
    check([c for c in did(calls, "pr", "edit", "7") if T_NEW in c], "the PR text catches up")
    check(did(calls, "workflow", "run"), "and the build that never started is started")

    print("the same, where the offer is an issue: the issue was never opened")
    w = World("retry-issue")
    w.branch_commit("portable/versions.env", pins("0.7.31"), BOT)
    out, calls = w.run(bash, T_NEW, "0.7.31", pr_create_fails=True)
    check(len(did(calls, "issue", "create")) == 1, "the missing issue is opened")
    check(did(calls, "workflow", "run"), "and the build is started")

    print("a person committed on the branch; their PR is open")
    w = World("person-open")
    w.branch_commit("portable/versions.env", pins("0.7.30"), BOT)
    tip = w.branch_commit("portable/Windows-Start.bat", "fix" + NL, PERSON, from_main=False)
    w.prs = [pr(7, "OPEN", T_OLD, tip)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(out.returncode == 0 and w.tip() == tip, "the person's work is left exactly as it was")
    check(not did(calls, "pr", "edit") and not did(calls, "workflow", "run"), "and the PR is not rewritten")

    print("a person amended the bot's commit (author stays the bot)")
    w = World("amend")
    w.branch_commit("portable/versions.env", pins("0.7.30"), BOT)
    tip = w.amend_as_person("portable/versions.env", pins("0.7.29"))
    w.prs = [pr(7, "OPEN", T_OLD, tip)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(w.tip() == tip, "an amend is recognised as a person's work by its committer")
    w = World("bot-named-launcher-fix")
    w.branch_commit("portable/versions.env", pins("0.7.30"), BOT)
    # Author and committer both the bot (a person using its identity, or a
    # tool replaying it) -- but it touches the launcher, which the bot never does.
    tip = w.branch_commit("portable/Windows-Start.bat", "fix" + NL, BOT, from_main=False)
    w.prs = [pr(7, "OPEN", T_OLD, tip)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(w.tip() == tip, "...and a change to any file but versions.env is a person's work even under the bot's name")

    print("a person's commit, then their PR was closed: the commits are kept by GitHub")
    w = World("person-closed")
    w.branch_commit("portable/versions.env", pins("0.7.30"), BOT)
    tip = w.branch_commit("portable/Windows-Start.bat", "fix" + NL, PERSON, from_main=False)
    w.prs = [pr(7, "CLOSED", T_OLD, tip)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(out.returncode == 0 and "HERMES_WEB_UI_VERSION=0.7.31" in w.proposed(),
          "the bot starts over instead of going quiet for good")
    check("Windows-Start.bat" not in git(w.root, "--git-dir", w.origin, "ls-tree", "-r", "--name-only", BRANCH),
          "...from main")
    check(len(did(calls, "pr", "create")) == 1, "...with a new PR")

    print("a person's commit that no PR keeps")
    w = World("person-unkept")
    w.branch_commit("portable/versions.env", pins("0.7.30"), BOT)
    tip = w.branch_commit("portable/Windows-Start.bat", "fix" + NL, PERSON, from_main=False)
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(w.tip() == tip, "work that exists only on the branch is never overwritten")
    check("::warning::" in out.stdout, "...and the run says so as a warning, not silently")

    print("this exact set was proposed and closed")
    w = World("declined")
    w.branch_commit("portable/versions.env", pins("0.7.31"), BOT)
    w.prs = [pr(7, "CLOSED", T_NEW)]
    tip = w.tip()
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(w.tip() == tip and not did(calls, "pr", "create"), "a declined offer is not made again")
    # GitHub offers "Delete branch" right after closing; then only the
    # declined check stands between the person's "no" and next week's PR.
    w = World("declined-branch-deleted")
    w.prs = [pr(7, "CLOSED", T_NEW)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(not w.tip() and not did(calls, "pr", "create") and not did(calls, "workflow", "run"),
          "...even after its branch was deleted")

    print("GitHub errors while the decline is being looked up")
    w = World("declined-lookup-fails")
    w.prs = [pr(7, "CLOSED", T_NEW)]
    out, calls = w.run(bash, T_NEW, "0.7.31", fail_on="pr list --head auto/update-pins --state closed")
    check(out.returncode != 0, "the step fails instead of reading the error as 'not declined'")
    check(not w.tip() and not did(calls, "pr", "create"), "...and offers nothing")

    print("Actions may not open PRs in this repository")
    w = World("no-pr-permission")
    out, calls = w.run(bash, T_NEW, "0.7.31", pr_create_fails=True)
    check(out.returncode == 0, "the step still succeeds (%s)" % tail(out))
    check(len(did(calls, "issue", "create")) == 1, "an issue is opened instead")
    check(did(calls, "workflow", "run"), "and the build still starts")
    body = w.gh_state("issues.json")[0]["body"]
    check("&title=%E8%B7%9F%E8%BF%9B" in body, "its link carries the exact title, URL-encoded")
    check(("本次提议：" + T_NEW) in body.split(NL), "it says which set of versions it offers")
    check("PR 正文" in body, "and carries the PR text")

    print("...the issue was closed: that is the 'no'")
    w = World("issue-declined")
    w.issues = [issue(30, "CLOSED", "本次提议：" + T_NEW)]
    out, calls = w.run(bash, T_NEW, "0.7.31", pr_create_fails=True)
    check(not w.tip() and not did(calls, "issue", "create") and not did(calls, "workflow", "run"),
          "a closed offer issue declines that exact set")
    w = World("issue-declined-other-set")
    w.issues = [issue(30, "CLOSED", "本次提议：" + T_OLD)]
    out, calls = w.run(bash, T_NEW, "0.7.31", pr_create_fails=True)
    check(len(did(calls, "issue", "create")) == 1, "...but not a different set")

    print("...the issue was closed because its PR was opened from the link")
    w = World("issue-closed-pr-open")
    w.issues = [issue(30, "CLOSED", "本次提议：" + T_NEW)]
    w.branch_commit("portable/versions.env", pins("0.7.31"), BOT)
    w.prs = [pr(8, "OPEN", T_NEW)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(did(calls, "pr", "edit", "8"), "an open PR means it was not declined: the PR gets the full text")
    check(not did(calls, "issue", "create"), "...and no new issue")

    print("...a week later, upstream moved and the bot's issue is still open")
    w = World("issue-second-week")
    w.issues = [issue(30, "OPEN", "本次提议：" + T_OLD)]
    w.branch_commit("portable/versions.env", pins("0.7.30"), BOT)
    out, calls = w.run(bash, T_NEW, "0.7.31", pr_create_fails=True)
    check(did(calls, "issue", "edit", "30"), "the open bot issue is updated")
    check(not did(calls, "issue", "create"), "...and no second issue is opened")

    print("the proposal was merged, then reverted on main; upstream has not moved")
    w = World("merged-then-reverted")
    w.branch_commit("portable/versions.env", pins("0.7.31"), BOT)
    w.merge_branch_then_revert()
    old_tip = w.tip()
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(out.returncode == 0 and w.tip() != old_tip and "HERMES_WEB_UI_VERSION=0.7.31" in w.proposed(),
          "a branch main already contains is not a proposal: it starts over from main")
    check(len(did(calls, "pr", "create")) == 1, "...and a PR is opened for it")

    print("Issues are switched off in the repository")
    w = World("issues-off")
    out, calls = w.run(bash, T_NEW, "0.7.31", issues_disabled=True)
    check(out.returncode == 0 and len(did(calls, "pr", "create")) == 1,
          "the PR is still opened: the issue lookups are skipped, not fatal (%s)" % tail(out))
    w = World("issues-off-no-pr")
    out, calls = w.run(bash, T_NEW, "0.7.31", issues_disabled=True, pr_create_fails=True)
    check(out.returncode != 0 and "::error::" in out.stdout,
          "with no PR and no issues allowed, the run fails and says why")

    print("a stranger filed an issue under the bot's title")
    w = World("stranger-issue")
    w.issues = [issue(40, "OPEN", "我的问题", by_bot=False)]
    out, calls = w.run(bash, T_NEW, "0.7.31", pr_create_fails=True)
    check(not did(calls, "issue", "edit", "40"), "the stranger's issue is never edited")
    check(len(did(calls, "issue", "create")) == 1, "the bot opens its own")
    # Another app's bot is a bot too; the author filter tells them apart.
    w = World("other-bot-issue")
    other = issue(41, "OPEN", "dependabot", by_bot=True)
    other["app"] = "dependabot"
    other["author"]["login"] = "app/dependabot"
    w.issues = [other]
    out, calls = w.run(bash, T_NEW, "0.7.31", pr_create_fails=True)
    check(not did(calls, "issue", "edit", "41"), "...nor another app's issue with that title")

    print("a fork opened a PR from a branch with the same name")
    w = World("fork")
    w.prs = [pr(9, "OPEN", "my feature", fork=True), pr(8, "CLOSED", T_NEW, fork=True)]
    out, calls = w.run(bash, T_NEW, "0.7.31")
    check(not did(calls, "pr", "edit"), "the fork's PR is never edited into the proposal")
    check(len(did(calls, "pr", "create")) == 1, "a fork's closed PR with our title is not taken as a decline")


def test_the_workflow_text():
    wf = io.open(WORKFLOW, encoding="utf-8").read()
    check("if: github.ref == 'refs/heads/main'" in wf, "the job only runs from main")
    check("pr merge" not in wf and "--admin" not in wf and "auto-merge" not in wf.lower(),
          "nothing in the workflow can merge")
    check("--force-with-lease" in wf and "push --force " not in wf and "push -f " not in wf,
          "a refresh is a lease-checked push, never a blind force")
    uses = [l.strip() for l in wf.split(NL) if "steps.plan.outputs.title" in l]
    check(uses == ["TITLE: ${{ steps.plan.outputs.title }}"],
          "upstream-derived text reaches the shell only through the environment")
    pipes = [l.strip() for l in wf.split(NL) if "$(gh" in l and "| grep" in l]
    check(not pipes, "no gh output is piped straight into grep (an API error would read as 'no match'): %s" % pipes)
    dev = io.open(os.path.join(REPO, "docs", "DEVELOPMENT.md"), encoding="utf-8").read()
    check(ISSUE_TITLE in dev, "DEVELOPMENT.md names the fallback issue by its real title")
    src = io.open(SCRIPT, encoding="utf-8").read()
    check('ISSUE_TITLE = "%s"' % ISSUE_TITLE in src, "...which is the one the script uses")


if __name__ == "__main__":
    if not os.path.isfile(WORKFLOW):
        print("  skip  not a source checkout")
        sys.exit(0)
    test_the_workflow_text()
    bash = find_bash()
    if not bash:
        print("  skip  no git bash here")
    else:
        try:
            scenarios(bash)
        finally:
            rmtree(TMP)
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")
