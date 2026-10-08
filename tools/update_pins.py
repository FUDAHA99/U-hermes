# -*- coding: utf-8 -*-
"""Propose upgrades to the pins in portable/versions.env. Never merges.

Run weekly by .github/workflows/update-pins.yml. It looks up what upstream has
released, rewrites versions.env for whatever can safely move, and writes a pull
request description with the release notes. A person reads that and decides.

Why a person: on 2026-10-08 the Web UI had moved 0.7.24 -> 0.7.31, and two of
those seven releases changed the product underneath us -- 0.7.26 made gateway
autostart opt-in, 0.7.30 made new chats default to its own "Ekko" agent instead
of Hermes. CI would have stayed green through both (it checks that the page
answers, not which agent a new chat talks to). So this script's job is to make
reviewing cheap: the notes of every release in range, each one read in full for
lines that touch what we depend on, and those lines pulled to the top.

What it will and will not move on its own:
  * Web UI     -- npm's latest. Crossing a minor line is called out.
  * AI engine  -- the newest upstream release whose pyproject still works on
                  our Python. Upstream main went 3.14-only right after
                  v2026.9.24, by gating every core dependency on
                  `python_version >= '3.14'` while requires-python still
                  admitted 3.11 -- so requires-python alone would have said yes.
                  A release like that is held back with the reason.
  * Node.js    -- newest LTS within the pinned major; a newer LTS major is
                  only reported.
  * uv         -- newest within the pinned 0.minor (uv's minor bumps are its
                  breaking ones: 0.8 changed the launcher format under us).
  * Python     -- newest patch of the pinned minor that has an embeddable
                  amd64 build; a newer minor moves with the engine, by hand.
Each proposed version is checked to have the download CI will fetch.

If any lookup fails, nothing is proposed that week. A partial answer would
change the offer -- drop a component the person already declined to have, or
re-offer one they declined -- for a reason that has nothing to do with upstream.

Usage:
  update_pins.py                      dry run: print the plan and the PR text
  update_pins.py --write [--body F] [--commit-msg F] [--github-output F]
Exit 0 whether or not anything changed; 1 only on a bug.
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

try:
    import tomllib
except ImportError:  # Python < 3.11; the workflow runs 3.13
    tomllib = None

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSIONS = os.path.join(ROOT, "portable", "versions.env")

BRANCH = "auto/update-pins"
# Where Actions may not open pull requests, the offer is an issue instead.
ISSUE_TITLE = "[自动] 上游有新版本，等你开 PR"
OFFER_MARK = "本次提议："   # + the PR title: which set of versions an issue offered
ENGINE_REPO = "NousResearch/hermes-agent"
WEBUI_REPO = "EKKOLearnAI/ekko-studio"
WEBUI_PACKAGE = "hermes-web-ui"
UV_REPO = "astral-sh/uv"
UV_WINDOWS_ASSET = "uv-x86_64-pc-windows-msvc.zip"
NODE_WINDOWS_FILE = "win-x64-zip"

# Lines in a release note that touch what U-Hermes depends on: how the gateway
# is started, which agent a chat uses, what is on by default, where state
# lives, what is exposed on the network, and the in-app updater that would
# bypass our pins. English words take word boundaries: a bare "auth" flagged
# every "co-authored" in the engine's contributor paragraph.
WATCH = re.compile(
    r"gateway|网关|HERMES_HOME|HERMES_BIN|singleton|默认|\bdefaults?\b|"
    r"端口|\bports?\b|\bprofiles?\b|登录|鉴权|\bauth(?:entication|orization)?\b|"
    r"\blog ?in\b|自动启动|autostart|\bruntimes?\b|运行时|更新|"
    r"\bupdat(?:e[sdr]?|ing)\b|\bupgrad\w*|self-update|"
    r"CORS|BIND_HOST|0\.0\.0\.0|数据库|迁移|\bmigrat|中转|\brelay\b|P2P|云端|\bcloud\b",
    re.IGNORECASE)
# Release-note boilerplate that matches the words above but is never news.
NOISE = re.compile(r"contributor|co-author|changed files|non-merge commits|merged PRs",
                   re.IGNORECASE)

# Section headings, best first: the curated summary, then the full change list.
HEADINGS = (("更新重点", "highlights"), ("更新内容",), ("what's changed", "changes"))

TITLES = {
    "HERMES_WEB_UI_VERSION": "网页界面",
    "HERMES_AGENT_REF": "AI 引擎",
    "NODE_VERSION": "Node.js",
    "UV_VERSION": "uv",
    "PYTHON_EMBED_VERSION": "Python（嵌入版）",
}
LIMIT = 60000            # GitHub refuses a PR description over 65536 characters
HEAD_LIMIT = 50000       # the always-kept part: summary, watch list, checklist, notices
HELD_SHOWN = 20
SHOW_BULLETS = 25        # per Web UI release; the rest is linked, never dropped silently
SHOW_LINES = 15          # per engine release
WATCH_PER_RELEASE = 8
RELEASE_PAGES = 5        # x100 releases: ekko-studio also tags apps and runtimes
ORDER = ("HERMES_WEB_UI_VERSION", "HERMES_AGENT_REF", "PYTHON_EMBED_VERSION",
         "UV_VERSION", "NODE_VERSION")


# --- network --------------------------------------------------------------
class Fetcher(object):
    """Every request goes through here, so the tests can swap it out."""

    def __init__(self, token=None, timeout=30):
        self.token = token
        self.timeout = timeout

    def _open(self, url, method="GET"):
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", "U-Hermes-update-pins")
        if self.token and url.startswith("https://api.github.com/"):
            req.add_header("Authorization", "Bearer " + self.token)
            req.add_header("Accept", "application/vnd.github+json")
        return urllib.request.urlopen(req, timeout=self.timeout)

    def text(self, url):
        with self._open(url) as r:
            return r.read().decode("utf-8")

    def json(self, url):
        return json.loads(self.text(url))

    def exists(self, url):
        try:
            with self._open(url, method="HEAD") as r:
                return 200 <= r.status < 300
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise  # a 429 or 5xx is "could not tell", not "does not exist"


def releases(f, repo, current_tag):
    """Published releases of `repo`, newest first, back to `current_tag`.

    Follows pages until the release we are pinned to has been seen, so the
    whole range is there however long the pin has sat. Returns (list, seen).
    """
    out = []
    for page in range(1, RELEASE_PAGES + 1):
        batch = f.json("https://api.github.com/repos/%s/releases?per_page=100&page=%d"
                       % (repo, page))
        out.extend(batch)
        if len(batch) < 100 or any(r.get("tag_name") == current_tag for r in batch):
            break
    seen = any(r.get("tag_name") == current_tag for r in out)
    return out, seen


def repo_file(f, repo, ref, path):
    """A file at a tag, through the API so the run's token covers it."""
    data = f.json("https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, ref))
    return base64.b64decode(data["content"]).decode("utf-8")


# --- versions -------------------------------------------------------------
def vtuple(text):
    """'v24.21.0' / '0.12.16' / 'v2026.9.24' -> (24, 21, 0); None otherwise."""
    m = re.match(r"^v?(\d+(?:\.\d+)*)$", (text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def vstr(t):
    return ".".join(str(p) for p in t)


def _cmp_clause(op, have, want):
    n = max(len(have), len(want))
    a = have + (0,) * (n - len(have))
    b = want + (0,) * (n - len(want))
    return {">=": a >= b, ">": a > b, "<=": a <= b, "<": a < b,
            "==": a == b, "!=": a != b}.get(op)


def spec_admits(spec, py):
    """Does a requires-python spec admit Python `py`?

    True / False, or None for a form this does not understand (wildcards,
    ~=, arbitrary equality) -- unknown is reported, never guessed.
    """
    result = True
    for clause in (spec or "").split(","):
        clause = clause.strip()
        if not clause:
            continue
        m = re.match(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)$", clause)
        if not m:
            return None
        result = result and _cmp_clause(m.group(1), py, vtuple(m.group(2)))
    return result


def marker_excludes(marker, py):
    """True if a dependency marker rules out Python `py`.

    Understands `python_version <op> 'X'` clauses joined by `and`. Anything
    else (or, platform checks) answers None -- "cannot tell", which the
    caller counts as not excluded.
    """
    if not marker.strip():
        return False
    if re.search(r"\bor\b", marker):
        return None
    admitted = True
    for clause in re.split(r"\band\b", marker):
        m = re.match(r"""^\s*python_version\s*(>=|<=|==|!=|>|<)\s*['"]([\d.]+)['"]\s*$""",
                     clause)
        if not m:
            return None
        admitted = admitted and _cmp_clause(m.group(1), py[:2], vtuple(m.group(2)))
    return not admitted


def engine_python_verdict(pyproject_text, py):
    """(works, reason) for running this engine release on Python `py`."""
    if tomllib is None:
        return False, "这台机器的 Python 没有 tomllib，无法判断"
    try:
        project = tomllib.loads(pyproject_text).get("project", {})
    except Exception as exc:
        return False, "读不懂它的 pyproject.toml（%s）" % exc.__class__.__name__
    spec = project.get("requires-python", "")
    admits = spec_admits(spec, py)
    if admits is False:
        return False, "要求 Python `%s`，不包括 %s" % (spec, vstr(py))
    if admits is None:
        return False, "看不懂它的 Python 要求 `%s`，需要人工确认" % spec
    deps = project.get("dependencies", [])
    gated = []
    for dep in deps:
        name, _, marker = dep.partition(";")
        if marker_excludes(marker, py):
            gated.append((name.strip(), marker.strip()))
    # A backport like `tomli; python_version < '3.11'` excluding us is normal.
    # Most of the core list excluding us is upstream moving to a newer Python.
    if deps and len(gated) * 2 > len(deps):
        floor = sorted({m for _n, m in gated})
        return False, ("%d/%d 个核心依赖只在更新的 Python 上安装（%s），"
                       "在 %s 上引擎会缺依赖起不来"
                       % (len(gated), len(deps), "；".join(floor[:2]), vstr(py)))
    return True, ""


# --- text from upstream -----------------------------------------------------
REDIRECT = "https://redirect.github.com/"


def _ref_link(repo, number):
    return "[%s#%s](%s%s/issues/%s)" % (repo, number, REDIRECT, repo, number)


def defuse(text, repo):
    """Quote upstream notes in our PR without side effects.

    Three side effects to avoid, in a public repository:
      * `@name` notifies that person from our repository;
      * a bare `#12` links to *our* issue 12, and leaves a mention on it;
      * any link to an upstream issue or PR -- a full URL, `owner/repo#12`,
        or our own rewrite of a bare `#12` -- puts a "mentioned this" event
        on that upstream PR, once per bot edit. Links through
        redirect.github.com still work and leave no trace; it is what
        Dependabot uses for the same reason.
    Boundaries are ASCII on purpose: GitHub's autolinker treats a CJK
    character as a boundary, so "感谢@name" and "问题#12" do link there,
    while Python's \\w would have called 谢 a word character and skipped them.
    """
    def outside_code(piece):
        piece = re.sub(r"https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+)/(pull|issues)/(\d+)",
                       REDIRECT + r"\1/\2/\3", piece)
        piece = re.sub(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9-]+/[A-Za-z0-9._-]+)#(\d+)\b",
                       lambda m: _ref_link(m.group(1), m.group(2)), piece)
        piece = re.sub(r"(?<![A-Za-z0-9_/&#\[])#(\d+)\b", lambda m: _ref_link(repo, m.group(1)), piece)
        return re.sub(r"(?<![A-Za-z0-9_`/.])@([A-Za-z0-9][A-Za-z0-9-]*)", r"`@\1`", piece)

    # Code spans are inert on GitHub already, and wrapping an @ inside one
    # (`npm i -g @scope/cli`) would close the span early and make it live.
    parts = re.split(r"(`[^`\n]*`)", text)
    return "".join(p if i % 2 else outside_code(p) for i, p in enumerate(parts))


def shorten(text, limit=280):
    """Cut at the last space before `limit`. A ref, a mention or a URL has no
    space in it, so this never splits one -- cutting after defuse() at a fixed
    offset once split a redirect link and left the bare ref live again."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip() + "…"


def sections(body):
    """[(heading, [bullets])] in order; bullets before any heading get ''."""
    out = [("", [])]
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        if re.match(r"^#{1,6}\s", line):
            out.append((line.lstrip("#").strip(), []))
        elif line.strip().startswith(("-", "*")):
            out[-1][1].append(line.strip())
    return out


def note_bullets(body):
    """Every bullet of the most useful section of a release note.

    Upstream's note format has changed three times in three months: "更新重点"
    (curated), "更新内容" + "Changes" (bilingual), and plain "What's Changed"
    -- 0.7.24 has only that, 31 entries long. Prefer the curated one, fall back
    in that order, and fall back to every bullet in the note.
    """
    found = sections(body)
    for names in HEADINGS:
        for heading, bullets in found:
            if bullets and any(n in heading.lower() for n in names):
                return bullets
    return [b for _h, bs in found for b in bs]


def flag(lines, prefix, cap=WATCH_PER_RELEASE):
    """The watch hits among `lines`, at most `cap`, plus a count of the rest."""
    hits = [l.lstrip("-* ").strip() for l in lines if WATCH.search(l) and not NOISE.search(l)]
    # GitHub's generated "What's Changed" entries end in "by @who in <url>";
    # in a list meant to be read top to bottom, that tail is only clutter.
    hits = [re.sub(r"\s+by\s+`?@[\w-]+`?\s+in\s+\S+\s*$", "", h) for h in hits]
    out = ["%s：%s" % (prefix, h) for h in hits[:cap]]
    if len(hits) > cap:
        out.append("%s：另有 %d 条相关条目，见它的发布说明" % (prefix, len(hits) - cap))
    return out


# --- components -----------------------------------------------------------
class Result(object):
    def __init__(self, key, current):
        self.key = key
        self.current = current
        self.proposed = None      # new pin, or None
        self.label = None         # human form of the new pin
        self.current_label = current
        self.engine_version = None
        self.notes = []           # shown beside the change
        self.held = []            # newer versions deliberately not taken
        self.changelog = []       # markdown lines
        self.watch = []           # lines that need a human's eyes
        self.notices = []         # what the reviewer must know about the notes themselves
        self.error = None         # a lookup failed: the whole run proposes nothing


def check_web_ui(f, current):
    r = Result("HERMES_WEB_UI_VERSION", current)
    reg = f.json("https://registry.npmjs.org/" + WEBUI_PACKAGE)
    latest = reg["dist-tags"]["latest"]
    cur, new = vtuple(current), vtuple(latest)
    if not new or not cur or new <= cur:
        return r
    r.proposed = r.label = latest
    if new[:2] != cur[:2]:
        r.notes.append("跨了版本线 %s → %s" % (vstr(cur[:2]), vstr(new[:2])))
    published = set(reg.get("versions", {}))
    rels, seen = releases(f, WEBUI_REPO, "v" + current)
    # The repo also tags its Android app (v1.0.x), runtime bundles and builds
    # never published to npm; only take versions of the package we install.
    picked = {}
    for rel in rels:
        tag = rel.get("tag_name", "")
        v = vtuple(tag)
        if rel.get("draft") or not v or tag.lstrip("v") not in published:
            continue
        if cur < v <= new:
            picked[v] = rel
    for v in sorted(picked):
        rel = picked[v]
        url = rel.get("html_url") or "https://github.com/%s/releases/tag/%s" % (WEBUI_REPO, rel["tag_name"])
        body = rel.get("body") or ""
        bullets = [defuse(b, WEBUI_REPO) for b in note_bullets(body)]
        r.changelog.append("**%s**（[发布说明](%s)）" % (vstr(v), url))
        r.changelog.extend(bullets[:SHOW_BULLETS])
        if len(bullets) > SHOW_BULLETS:
            r.changelog.append("- ……另有 %d 条，见[发布说明](%s)" % (len(bullets) - SHOW_BULLETS, url))
        r.changelog.append("")
        # The watch list reads every bullet of every section -- the full
        # "What's Changed" list too, not just the summary that is shown:
        # 0.7.25's summary left out "fix group Coding Agent MCP authentication".
        everything = [defuse(b, WEBUI_REPO) for _h, bs in sections(body) for b in bs]
        r.watch.extend(flag(everything, "网页界面 %s" % vstr(v)))
    wanted = sorted(vtuple(x) for x in published if vtuple(x) and cur < vtuple(x) <= new)
    missing = [v for v in wanted if v not in picked]
    if missing:
        where = "" if seen else "（只读了最近 %d 页发布，更早的没有读到）" % RELEASE_PAGES
        r.notices.append("网页界面：这几个版本在 GitHub 的发布里没有找到说明%s，所以也没有逐条检查：%s。"
                         "见 https://github.com/%s/releases"
                         % (where, "、".join(vstr(v) for v in missing), WEBUI_REPO))
    return r


def _engine_version(rel):
    m = re.search(r"v(\d+\.\d+\.\d+)", rel.get("name") or "")
    return m.group(1) if m else None


def check_engine(f, current_ref, py):
    r = Result("HERMES_AGENT_REF", current_ref)
    rels, _seen = releases(f, ENGINE_REPO, current_ref)
    cur = vtuple(current_ref)
    for rel in rels:
        if rel.get("tag_name") == current_ref and _engine_version(rel):
            r.current_label = "%s（%s）" % (_engine_version(rel), current_ref)
    usable = [rel for rel in rels
              if not rel.get("draft") and not rel.get("prerelease")
              and vtuple(rel.get("tag_name")) and cur and vtuple(rel["tag_name"]) > cur]
    usable.sort(key=lambda rel: vtuple(rel["tag_name"]))
    chosen = None
    for rel in reversed(usable):
        ok, why = engine_python_verdict(
            repo_file(f, ENGINE_REPO, rel["tag_name"], "pyproject.toml"), py)
        if ok:
            chosen = rel
            break
        r.held.append("AI 引擎 %s（%s）：%s"
                      % (_engine_version(rel) or rel["tag_name"], rel["tag_name"], why))
    if not chosen:
        return r
    tag = chosen["tag_name"]
    r.proposed = tag
    r.engine_version = _engine_version(chosen)
    r.label = "%s（%s）" % (r.engine_version or tag, tag)
    # Each engine release note covers only its own window, so a jump over
    # several releases needs all of them: 0.21.4's note was the one that
    # announced the gateway singleton lock we had to work around.
    for rel in (x for x in usable if vtuple(x["tag_name"]) <= vtuple(tag)):
        ver = _engine_version(rel) or rel["tag_name"]
        url = rel.get("html_url") or "https://github.com/%s/releases/tag/%s" % (ENGINE_REPO, rel["tag_name"])
        raw = (rel.get("body") or "").replace("\r\n", "\n")
        # The "Updating" section is the same install boilerplate every time.
        raw_notes = re.split(r"\n#+\s*Updating\b", raw)[0]
        notes = defuse(raw_notes, ENGINE_REPO)
        lines = [l for l in notes.split("\n") if l.strip()]
        r.changelog.append("**%s（%s）**（[发布说明](%s)）" % (ver, rel["tag_name"], url))
        # Quoted, so upstream's own headings do not restructure our PR.
        r.changelog.extend("> " + l for l in lines[:SHOW_LINES])
        if len(lines) > SHOW_LINES:
            r.changelog.append("> ……（还有 %d 行，见发布说明）" % (len(lines) - SHOW_LINES))
        r.changelog.append("")
        # Engine notes come as long paragraphs; judge them clause by clause.
        # A long clause is shortened for display, never dropped: the long
        # ones are often the most relevant, and dropping them also left them
        # out of the "另有 N 条" count. Shortened first, defused after.
        clauses = [defuse(shorten(c), ENGINE_REPO)
                   for c in (c.strip(" -*#>") for c in re.split(r";\s+|\.\s+|\n", raw_notes)) if c]
        r.watch.extend(flag(clauses, "AI 引擎 %s" % ver))
    r.changelog.append("完整对比：https://github.com/%s/compare/%s...%s"
                       % (ENGINE_REPO, current_ref, tag))
    return r


def check_node(f, current):
    r = Result("NODE_VERSION", current)
    index = f.json("https://nodejs.org/dist/index.json")
    cur = vtuple(current)
    if not cur:
        return r
    same = [rel for rel in index
            if rel.get("lts") and vtuple(rel["version"])
            and vtuple(rel["version"])[0] == cur[0]
            and NODE_WINDOWS_FILE in rel.get("files", [])]
    best = max(same, key=lambda rel: vtuple(rel["version"]), default=None)
    if best and vtuple(best["version"]) > cur:
        r.proposed = r.label = best["version"]
        between = [rel for rel in same if cur < vtuple(rel["version"]) <= vtuple(best["version"])]
        if any(rel.get("security") for rel in between):
            r.notes.append("其中包含安全修复")
        r.changelog.append("https://github.com/nodejs/node/blob/main/doc/changelogs/CHANGELOG_V%d.md"
                           % cur[0])
    majors = sorted({vtuple(rel["version"])[0] for rel in index
                     if rel.get("lts") and vtuple(rel["version"])
                     and vtuple(rel["version"])[0] > cur[0]})
    if majors:
        r.held.append("Node.js v%d 已经是 LTS，但不自动跨大版本" % majors[-1])
    return r


def check_uv(f, current):
    r = Result("UV_VERSION", current)
    cur = vtuple(current)
    if not cur:
        return r
    rels, _seen = releases(f, UV_REPO, current)

    def usable(rel):
        return (not rel.get("draft") and not rel.get("prerelease")
                and vtuple(rel.get("tag_name"))
                and any(a.get("name") == UV_WINDOWS_ASSET for a in rel.get("assets", [])))

    line = [rel for rel in rels if usable(rel) and vtuple(rel["tag_name"])[:2] == cur[:2]]
    best = max(line, key=lambda rel: vtuple(rel["tag_name"]), default=None)
    if best and vtuple(best["tag_name"]) > cur:
        r.proposed = r.label = best["tag_name"]
        for rel in sorted(line, key=lambda rel: vtuple(rel["tag_name"])):
            v = vtuple(rel["tag_name"])
            if cur < v <= vtuple(best["tag_name"]):
                breaking = "breaking" in (rel.get("body") or "").lower()
                r.changelog.append("- [%s](%s)%s" % (rel["tag_name"], rel.get("html_url", ""),
                                                     "（含 Breaking changes）" if breaking else ""))
                if breaking:
                    r.watch.append("uv %s 的发布说明里有 Breaking changes" % rel["tag_name"])
    newer = [rel for rel in rels if usable(rel) and vtuple(rel["tag_name"])[:2] > cur[:2]]
    if newer:
        top = max(newer, key=lambda rel: vtuple(rel["tag_name"]))
        r.held.append("uv %s 是新的 0.x 版本线（uv 在这里做不兼容改动），不自动升" % top["tag_name"])
    return r


def check_python(f, current):
    r = Result("PYTHON_EMBED_VERSION", current)
    cur = vtuple(current)
    if not cur:
        return r
    listing = f.text("https://www.python.org/ftp/python/")
    found = sorted({vtuple(v) for v in re.findall(r'href="(\d+\.\d+\.\d+)/"', listing)},
                   reverse=True)

    def has_embed(v):
        s = vstr(v)
        return f.exists("https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip" % (s, s))

    for v in (v for v in found if v[:2] == cur[:2] and v > cur):
        if has_embed(v):
            r.proposed = r.label = vstr(v)
            r.changelog.append("https://docs.python.org/release/%s/whatsnew/changelog.html" % vstr(v))
            break
    for v in (v for v in found if v[:2] > cur[:2]):
        if has_embed(v):
            r.held.append("Python %s 有嵌入版，但换小版本要跟引擎一起手动升（见 versions.env）"
                          % vstr(v))
            break
    return r


# --- versions.env ---------------------------------------------------------
def read_pins(text):
    pins = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            pins[m.group(1)] = m.group(2)
    return pins


def rewrite(text, changes, engine_version=None):
    """Change only the values named in `changes`; keep every comment.

    The engine pin carries a "# vTAG == hermes-agent X" line right above it;
    that is rewritten too, so the file never describes a version it no
    longer pins.
    """
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m or m.group(1) not in changes:
            continue
        key = m.group(1)
        lines[i] = "%s=%s" % (key, changes[key])
        if key == "HERMES_AGENT_REF" and engine_version and i > 0 \
                and re.match(r"^# \S+ == hermes-agent \S+$", lines[i - 1]):
            lines[i - 1] = "# %s == hermes-agent %s" % (changes[key], engine_version)
    return nl.join(lines)


# --- the plan -------------------------------------------------------------
def _run(key, current, step):
    try:
        return step()
    except Exception as exc:
        r = Result(key, current)
        r.error = "%s: %s" % (exc.__class__.__name__, str(exc)[:200])
        return r


def plan(f, pins):
    results = {
        "HERMES_WEB_UI_VERSION": _run("HERMES_WEB_UI_VERSION", pins["HERMES_WEB_UI_VERSION"],
                                      lambda: check_web_ui(f, pins["HERMES_WEB_UI_VERSION"])),
        "PYTHON_EMBED_VERSION": _run("PYTHON_EMBED_VERSION", pins["PYTHON_EMBED_VERSION"],
                                     lambda: check_python(f, pins["PYTHON_EMBED_VERSION"])),
        "UV_VERSION": _run("UV_VERSION", pins["UV_VERSION"], lambda: check_uv(f, pins["UV_VERSION"])),
        "NODE_VERSION": _run("NODE_VERSION", pins["NODE_VERSION"],
                             lambda: check_node(f, pins["NODE_VERSION"])),
    }
    # The engine is judged against the Python this proposal would ship.
    py = vtuple(results["PYTHON_EMBED_VERSION"].proposed or pins["PYTHON_EMBED_VERSION"])
    results["HERMES_AGENT_REF"] = _run("HERMES_AGENT_REF", pins["HERMES_AGENT_REF"],
                                       lambda: check_engine(f, pins["HERMES_AGENT_REF"], py))
    return [results[k] for k in ORDER]


def render(results, repo="FUDAHA99/U-hermes"):
    """(title, PR body, commit message)."""
    errors = [r for r in results if r.error]
    moved = [] if errors else [r for r in results if r.proposed]
    names = "、".join("%s %s" % (TITLES[r.key], r.label) for r in moved)
    title = ("跟进上游版本：" + names) if moved else (
        "这周查询失败，没有提议" if errors else "上游没有可以自动升级的版本")
    out = []
    watch_at = None
    if errors:
        out += ["## 查询失败，这周不提议任何升级", ""]
        out += ["- %s：%s" % (TITLES[r.key], r.error) for r in errors]
        out += ["", "只查到一部分就提议，会让这次的提议跟上次不一样（漏掉一个组件，"
                "或者把关掉过的又提一遍），所以干脆不提。下周会再查。", ""]
    if moved:
        out += ["## 这次升级", "", "| 组件 | 现在 | 升到 | 备注 |", "|---|---|---|---|"]
        for r in moved:
            out.append("| %s | %s | **%s** | %s |"
                       % (TITLES[r.key], r.current_label, r.label, "；".join(r.notes)))
        out.append("")
        watch = [w for r in moved for w in r.watch]
        if watch:
            out += ["## ⚠ 可能影响我们的改动（合并前请逐条确认）", ""]
            watch_at = (len(out), len(out) + len(watch))
            out += ["- " + w for w in watch]
            out += ["", "这些是把每个版本的发布说明全部读过（网页界面连摘要之外的完整改动列表也读了）、"
                    "按关键词挑出来的（网关、默认、端口、登录、运行时、更新、中转……），可能有误报或漏报。"
                    "下面「发布说明」里列的是每个版本的摘要，完整内容点各版本的发布说明链接。", ""]
        build = ("https://github.com/%s/actions/workflows/release.yml?query=branch%%3A%s"
                 % (repo, BRANCH.replace("/", "%2F")))
        out += ["## 合并前", "",
                "这个 PR 页面上**看不到构建结果**（机器人触发的构建不挂在 PR 上），请点这里看："
                "[本分支的构建](%s)" % build, "",
                "- [ ] 构建：Windows 必须绿（macOS 红在「校验压缩包」是已知的，不用管）",
                "- [ ] 上面每条 ⚠ 都确认过：不影响，或已经在这个分支上改好",
                "- [ ] 在 U 盘上**真的装上新版本**再测（只同步代码不会换版本，"
                "`runtime\\` 和 `hermes\\` 不在同步范围里）：",
                # -B from the remote: a plain checkout of a branch fetched in an
                # earlier week stays on that week's proposal, and step 4 would
                # then compare the stick with that same stale versions.env.
                "  1. `git fetch origin` 然后 `git checkout -B %s origin/%s`" % (BRANCH, BRANCH),
                "  2. `powershell -NoProfile -ExecutionPolicy Bypass -File tools\\sync-to-instance.ps1 "
                "-Target H:\\Hermes -Apply`",
                "  3. `powershell -NoProfile -ExecutionPolicy Bypass -File H:\\Hermes\\setup.ps1 -Force`"
                "（按新版本号重装运行环境和引擎，不碰 `data\\`。**注意**：网页界面自带的 Ekko "
                "智能体把数据存在 `runtime\\` 里它自己的 `.ekko\\` 下，重装会清掉；"
                "U 盘上如果有 Ekko 的会话，先备份）",
                "  4. `H:\\Hermes\\hermes\\.venv\\Scripts\\python.exe H:\\Hermes\\scripts\\version_check.py`"
                "：五项都是 OK，而且「钉住」那一列和上面「这次升级」表里的版本一致",
                "  5. 双击 `Windows-Start.bat`，新建一个聊天：回复来自 Hermes（不是网页界面自带的 Ekko），"
                "并且网关在 8642 端口上",
                ""]
    held = [h for r in results for h in r.held]
    if held:
        out += ["## 没有自动升级的", ""]
        # Grows by about two engine releases a week while a Python move is
        # pending; the newest are what matter.
        out += ["- " + h for h in held[:HELD_SHOWN]]
        if len(held) > HELD_SHOWN:
            out.append("- ……另有 %d 个更早的版本，原因相同或类似" % (len(held) - HELD_SHOWN))
        out.append("")
    notices = [n for r in moved for n in r.notices]
    if notices:
        out += ["## 注意", ""]
        out += ["- " + n for n in notices]
        out.append("")
    out += ["## 关于这个 PR", "",
            "由 `.github/workflows/update-pins.yml` 每周自动生成，**不会自动合并**。"
            "关掉这个 PR 就是不要这一组升级：完全相同的一组版本不会再提；"
            "但只要上游任何一个组件又出了新版本，新的一组（可能还包括这次的某些版本）会重新提议。"
            "在这个分支上提交了修改，只要 PR 还开着，机器人就不会动它。", ""]
    head = "\n".join(out) + "\n"
    # The head is kept whole, so it needs a budget of its own: the watch list
    # is the part that grows with the range. Past the budget, drop its tail
    # and say how many are left out -- a body over GitHub's limit is refused,
    # and then nothing reaches the reviewer at all.
    if watch_at and len(head) > HEAD_LIMIT:
        start, end = watch_at
        shown = out[start:end]
        while shown and len(head) > HEAD_LIMIT:
            shown = shown[:-1]
            out[start:end] = shown + ["- ……另有 %d 条，列不下了，见各版本的发布说明"
                                      % (len(watch) - len(shown))]
            end = start + len(shown) + 1
            head = "\n".join(out) + "\n"
    # Everything above is short and always kept; only the release notes
    # below are cut to fit GitHub's limit, so a long range can never take the
    # checklist, the notices or the footer with it.
    notes = []
    logs = [r for r in moved if r.changelog]
    if logs:
        notes += ["## 发布说明", ""]
        for r in logs:
            notes += ["### %s %s → %s" % (TITLES[r.key], r.current_label, r.label), ""]
            notes += r.changelog
            notes.append("")
    tail = "\n".join(notes) + "\n" if notes else ""
    room = LIMIT - len(head)
    if len(tail) > room:
        cut = tail.rfind("\n", 0, max(0, room - 200))
        tail = tail[:max(cut, 0)] + "\n\n……（发布说明太长，后面截掉了，请看各组件的发布页）\n"
    body = head + tail
    commit = title + "\n\n" + "\n".join(
        "%s: %s -> %s" % (r.key, r.current, r.proposed) for r in moved) + "\n"
    return title, body, commit


def bot_issue(issues, proposing=None):
    """The bot's fallback issue among `issues` (gh issue list JSON), as a number.

    Only one this workflow opened: same title, and a bot author. The repo is
    public; anyone can file an issue with that title, and editing theirs
    would put our text under their name. With `proposing`, only an issue
    that offered exactly that set of versions -- how a closed issue counts
    as a "no" to that set.
    """
    for i in issues:
        author = i.get("author") or {}
        # gh reports the Actions bot as {"is_bot": true, "login": "app/github-actions"}.
        if i.get("title") != ISSUE_TITLE or not author.get("is_bot") or \
                author.get("login") not in ("app/github-actions", "github-actions[bot]", "github-actions"):
            continue
        if proposing is None or OFFER_MARK + proposing in (i.get("body") or "").splitlines():
            return str(i["number"])
    return ""


def issue_body(title, pr_body, repo, branch=BRANCH):
    """The fallback issue. Its link carries the exact title, so a PR opened
    from it can be declined, and recognised, exactly like one the bot opened."""
    from urllib.parse import quote
    link = "https://github.com/%s/compare/main...%s?expand=1&title=%s" % (repo, branch, quote(title))
    return "\n".join([
        "这个仓库不允许 GitHub Actions 开 PR，所以没能自动开。分支 `%s` 已经推好，" % branch,
        "点这里开 PR：" + link,
        "",
        "不要这一组升级：直接关掉这个 issue（完全相同的一组版本不会再提）。",
        "（想让它直接开 PR：Settings → Actions → General → 勾选 Allow GitHub Actions to create "
        "and approve pull requests）",
        "",
        OFFER_MARK + title,
        "",
        pr_body,
    ])


def issue_cli(argv):
    """`update_pins.py issue title|find|body` -- the workflow's issue helpers,
    here rather than inline in YAML so they are tested like the rest."""
    ap = argparse.ArgumentParser(prog="update_pins.py issue")
    ap.add_argument("action", choices=("title", "find", "body"))
    ap.add_argument("--proposing", help="find: only an issue that offered this title")
    ap.add_argument("--body-file", help="body: the PR description to include")
    args = ap.parse_args(argv)
    if args.action == "title":
        print(ISSUE_TITLE)
    elif args.action == "find":
        print(bot_issue(json.load(sys.stdin), args.proposing))
    else:
        with io.open(args.body_file, encoding="utf-8") as fh:
            pr_body = fh.read()
        sys.stdout.write(issue_body(os.environ["TITLE"], pr_body,
                                    os.environ.get("GITHUB_REPOSITORY") or "FUDAHA99/U-hermes"))
    return 0


def main(argv=None, fetcher=None, versions_path=VERSIONS):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["issue"]:
        return issue_cli(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="rewrite versions.env")
    ap.add_argument("--body", help="write the PR description here")
    ap.add_argument("--commit-msg", help="write the commit message here")
    ap.add_argument("--github-output", help="append changed=/title= here")
    args = ap.parse_args(argv)

    with io.open(versions_path, encoding="utf-8", newline="") as fh:
        original = fh.read()
    pins = read_pins(original)
    f = fetcher or Fetcher(token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    results = plan(f, pins)
    title, body, commit = render(results, os.environ.get("GITHUB_REPOSITORY") or "FUDAHA99/U-hermes")
    errors = [r for r in results if r.error]
    changes = {} if errors else {r.key: r.proposed for r in results if r.proposed}
    engine = next(r for r in results if r.key == "HERMES_AGENT_REF")
    updated = rewrite(original, changes, engine.engine_version)

    if args.write and updated != original:
        with io.open(versions_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
    # newline="\n" everywhere: on a Windows runner text mode would write
    # "changed=true\r", which never equals 'true' in the workflow's `if:`.
    for path, text in ((args.body, body), (args.commit_msg, commit)):
        if path:
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    if args.github_output:
        with io.open(args.github_output, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("changed=%s\n" % ("true" if changes else "false"))
            fh.write("title=%s\n" % title.replace("\n", " ").replace("\r", " "))
        for r in errors:
            print("::warning::%s lookup failed, nothing proposed this week: %s"
                  % (r.key, r.error.replace("\n", " ")))
    if not (args.body or args.github_output):
        print(title)
        print()
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
