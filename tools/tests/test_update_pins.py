# -*- coding: utf-8 -*-
"""tools/update_pins.py, against recorded shapes of every upstream it reads.

No network: a fake fetcher answers from fixtures. Each fixture carries a trap
that was real on 2026-10-08 -- the Web UI repository also tags its Android app
and runtime bundles, upstream main gated every core dependency on Python 3.14
while requires-python still admitted 3.11, a uv release can lack the Windows
asset CI downloads, a Python patch can exist before its embeddable build does,
the Web UI's note format changed three times, and an engine jump can span
several releases whose notes each cover only their own window.

The shell step that pushes and opens the PR is tested separately, by running
it: tools/tests/test_update_pins_workflow.py.

Run:  python tools/tests/test_update_pins.py
"""
import base64
import importlib.util
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)

_spec = importlib.util.spec_from_file_location("update_pins", os.path.join(TOOLS, "update_pins.py"))
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)

FAILURES = []
NL = chr(10)


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


# --- fixtures -------------------------------------------------------------
PY_COMPAT = """
[project]
name = "hermes-agent"
requires-python = ">=3.11,<3.14"
dependencies = ["openai==2.24.0", "ruamel.yaml==0.18.17", "rich==14.3.3"]
"""
# What upstream main looked like: requires-python still admits 3.11, but the
# core dependencies only install on 3.14.
PY_GATED = """
[project]
name = "hermes-agent"
requires-python = ">=3.11,<3.15"
dependencies = [
  "openai==2.24.0; python_version >= '3.14'",
  "ruamel.yaml==0.18.16; python_version >= '3.14'",
  "rich==14.3.3; python_version >= '3.14'",
  "jinja2==3.1.6",
]
"""
PY_BACKPORT = """
[project]
requires-python = ">=3.11"
dependencies = ["openai==2.24.0", "rich==14.3.3", "tomli==2.0.1; python_version < '3.11'"]
"""
PY_STRICT = """
[project]
requires-python = ">=3.14"
dependencies = ["openai==2.24.0"]
"""
PY_COMPATIBLE_OP = """
[project]
requires-python = "~=3.11"
dependencies = []
"""
PYPROJECT_AT = {"v2026.10.5": PY_GATED, "v2026.9.24": PY_COMPAT,
                "v2026.9.21": PY_COMPAT, "v2026.9.14": PY_COMPAT}

WEBUI_NPM = {
    "dist-tags": {"latest": "0.7.31"},
    "versions": {v: {} for v in ("0.7.23", "0.7.24", "0.7.25", "0.7.26", "0.7.30", "0.7.31")},
}


def _note(*bullets):
    return "## 更新重点" + NL + NL.join("- " + b for b in bullets) + NL + "## What's Changed" + NL + \
        "* chore: bump by @EKKOLearnAI in https://github.com/x/y/pull/1"


WEBUI_RELEASES = [
    {"tag_name": "v0.7.31", "body": _note("新增 App 与 Studio 的 P2P 直连，直连不可用时自动回退云端中转 (#3290)")},
    {"tag_name": "v1.0.6", "body": _note("Android 版：不该出现在网页界面的更新日志里")},
    {"tag_name": "v0.7.30", "body": _note("新建聊天默认选择 Ekko (#3284)", "修复按钮颜色，感谢 @someone")},
    {"tag_name": "v0.7.26", "body": _note("Gateway 自动启动改为显式开启", "优化毛玻璃效果")},
    {"tag_name": "v0.7.25", "body": _note("新增 Cursor CLI")},
    {"tag_name": "v0.7.24", "body": _note("已经钉住的版本，不该再出现")},
    # In range, but never published to npm (a desktop-only build): not
    # something we install, so not in the notes. This, not the out-of-range
    # v1.0.6 above, is what exercises the npm filter.
    {"tag_name": "v0.7.28", "body": _note("只在 GitHub 上发的版本")},
    # A draft that shares a published version's tag: only the draft filter
    # keeps its text out.
    {"tag_name": "v0.7.26", "draft": True, "body": _note("草稿")},
]

ENGINE_RELEASES = [
    {"tag_name": "v2026.10.5", "name": "Hermes Agent v0.22.0 (v2026.10.5)", "body": "big"},
    {"tag_name": "v2026.10.1", "name": "Hermes Agent v0.22.0rc1 (v2026.10.1)", "prerelease": True},
    {"tag_name": "v2026.9.24", "name": "Hermes Agent v0.21.5 (v2026.9.24)",
     "body": "# Hermes Agent v0.21.5" + NL +
             "Patch release; per-profile restart and `gateway.standalone` to opt out. Thanks @dev (#126740)" + NL +
             "**140 contributors** appear in commits, co-author trailers and reviews" + NL +
             # Each of these two trips exactly one defence: "gateway" is a
             # real keyword in a contributor line (NOISE must drop it), and
             # "author" must not pass for "auth" (word boundaries).
             "Thanks to 140 contributors across the gateway and CLI" + NL +
             "The author field in exported transcripts is fixed" + NL +
             "Full changelog: https://github.com/NousResearch/hermes-agent/compare/v2026.9.21...v2026.9.24" + NL +
             "## Updating" + NL + "- `hermes update` (git installs)"},
    {"tag_name": "v2026.9.21", "name": "Hermes Agent v0.21.4 (v2026.9.21)",
     "body": "# Hermes Agent v0.21.4" + NL + "Adds a host-wide gateway singleton lock with a rendezvous record." +
             NL + "Desktop gains one-click local engine updates" +
             NL + "A very long clause about the gateway " + "and its many moving parts " * 15 +
             # Long, keyword-bearing and full of refs: shortened before it is
             # defused, or the cut lands inside a redirect link.
             NL + "Gateway fixes " + " ".join("(#%d)" % (106900 + i) for i in range(40)) +
             NL + NL.join("- line %d" % i for i in range(30))},
    {"tag_name": "v2026.9.14", "name": "Hermes Agent v0.21.3 (v2026.9.14)", "body": "older"},
]


def node(v, lts=True, security=False, win=True):
    files = ["win-x64-zip", "linux-x64"] if win else ["linux-x64"]
    return {"version": v, "lts": "Krypton" if lts else False, "security": security, "files": files}


NODE_INDEX = [
    node("v26.11.1", lts="Lts26"),
    node("v24.23.0", win=False),           # no Windows zip yet: not usable
    node("v24.22.0", security=True),
    node("v24.21.0"),
    node("v25.9.0", lts=False),
]


def uv(tag, win=True, body=""):
    assets = [{"name": "uv-x86_64-pc-windows-msvc.zip"}] if win else [{"name": "uv-aarch64-apple-darwin.tar.gz"}]
    return {"tag_name": tag, "assets": assets, "body": body, "html_url": "https://github.com/astral-sh/uv/releases/tag/" + tag}


UV_RELEASES = [
    uv("0.13.0"),
    uv("0.12.24", win=False),
    uv("0.12.23"),
    uv("0.12.20", body="### Breaking changes" + NL + "- something"),
    uv("0.12.16"),
]

PY_LISTING = "".join('<a href="%s/">%s/</a>' % (v, v)
                     for v in ("3.12.9", "3.13.15", "3.13.16", "3.13.17", "3.14.8", "3.15.0"))
PY_EMBEDS = {"3.13.15", "3.13.16", "3.14.8"}  # 3.13.17 and 3.15.0: no embed build yet


class Fake(object):
    """Answers like the real services, paginating GitHub releases 100 at a time."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.fetched = []
        self.releases = {
            up.WEBUI_REPO: list(WEBUI_RELEASES),
            up.ENGINE_REPO: list(ENGINE_RELEASES),
            up.UV_REPO: list(UV_RELEASES),
        }
        self.npm = json.loads(json.dumps(WEBUI_NPM))
        self.pyprojects = dict(PYPROJECT_AT)

    def _guard(self, url):
        self.fetched.append(url)
        for f in self.fail:
            if f in url:
                raise IOError("simulated outage: " + f)

    def json(self, url):
        self._guard(url)
        if url == "https://registry.npmjs.org/hermes-web-ui":
            return json.loads(json.dumps(self.npm))
        if url == "https://nodejs.org/dist/index.json":
            return json.loads(json.dumps(NODE_INDEX))
        for repo, rels in self.releases.items():
            prefix = "https://api.github.com/repos/%s/releases?per_page=100&page=" % repo
            if url.startswith(prefix):
                page = int(url[len(prefix):])
                return json.loads(json.dumps(rels[(page - 1) * 100:page * 100]))
        prefix = "https://api.github.com/repos/%s/contents/pyproject.toml?ref=" % up.ENGINE_REPO
        if url.startswith(prefix):
            text = self.pyprojects[url[len(prefix):]]
            return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        raise KeyError(url)

    def text(self, url):
        self._guard(url)
        if url == "https://www.python.org/ftp/python/":
            return PY_LISTING
        raise KeyError(url)

    def exists(self, url):
        self._guard(url)
        return any(url.endswith("/python-%s-embed-amd64.zip" % v) for v in PY_EMBEDS)


PINS = {
    "PYTHON_EMBED_VERSION": "3.13.15",
    "NODE_VERSION": "v24.21.0",
    "UV_VERSION": "0.12.16",
    "HERMES_WEB_UI_VERSION": "0.7.24",
    "HERMES_AGENT_REF": "v2026.9.21",
}

VERSIONS_ENV = NL.join([
    "# Single source of truth.",
    "PYTHON_EMBED_VERSION=3.13.15",
    "",
    "# Node comment stays.",
    "NODE_VERSION=v24.21.0",
    "UV_VERSION=0.12.16",
    "HERMES_WEB_UI_VERSION=0.7.24",
    "# BUMP THIS TOGETHER WITH PYTHON_EMBED_VERSION",
    "# v2026.9.21 == hermes-agent 0.21.4",
    "HERMES_AGENT_REF=v2026.9.21",
    "",
])


# --- tests ----------------------------------------------------------------
def test_versions_and_specs():
    check(up.vtuple("v24.21.0") == (24, 21, 0) and up.vtuple("v2026.9.24") == (2026, 9, 24),
          "versions and calendar tags parse")
    check(up.vtuple("v2026.10.1") > up.vtuple("v2026.9.24"), "October sorts after September")
    check(up.vtuple("0.22.0rc1") is None and up.vtuple("") is None,
          "a pre-release or empty string is not a version")
    py313 = (3, 13, 16)
    check(up.spec_admits(">=3.11,<3.14", py313) is True, ">=3.11,<3.14 admits 3.13")
    check(up.spec_admits(">=3.11,<3.14", (3, 14, 0)) is False, "...and not 3.14")
    check(up.spec_admits(">=3.14", py313) is False, ">=3.14 excludes 3.13")
    check(up.spec_admits("~=3.11", py313) is None, "a form it does not understand is unknown, not yes")
    check(up.marker_excludes("python_version >= '3.14'", py313) is True, "a 3.14 gate excludes 3.13")
    check(up.marker_excludes("python_version < '3.11'", py313) is True, "a backport gate excludes 3.13 too")
    check(up.marker_excludes("sys_platform == 'win32'", py313) is None, "a platform marker is not judged")
    check(up.marker_excludes("", py313) is False, "no marker excludes nothing")


def test_engine_verdict():
    py = (3, 13, 16)
    ok, _ = up.engine_python_verdict(PY_COMPAT, py)
    check(ok, "0.21.5's pyproject runs on 3.13")
    ok, why = up.engine_python_verdict(PY_GATED, py)
    check(not ok, "upstream main's pyproject is refused although requires-python admits 3.13")
    check("3.14" in why and "3/4" in why, "...and the reason names the gate and how much it covers")
    ok, _ = up.engine_python_verdict(PY_BACKPORT, py)
    check(ok, "one backport marker excluding us is normal, not a block")
    ok, _ = up.engine_python_verdict(PY_STRICT, py)
    check(not ok, "requires-python >=3.14 is refused")
    ok, why = up.engine_python_verdict(PY_COMPATIBLE_OP, py)
    check(not ok and "人工" in why, "a requires-python it cannot read asks a person instead of guessing")
    ok, _ = up.engine_python_verdict("not = [valid toml", py)
    check(not ok, "an unreadable pyproject is refused")


def test_engine_picks_the_newest_release_that_runs():
    f = Fake()
    r = up.check_engine(f, "v2026.9.21", (3, 13, 16))
    check(r.proposed == "v2026.9.24", "skips the 3.14-only release and takes 0.21.5")
    check(r.label == "0.21.5（v2026.9.24）", "...labelled with the engine version")
    check(r.current_label == "0.21.4（v2026.9.21）", "the current pin is labelled too")
    check(any("v2026.10.5" in h and "3.14" in h for h in r.held),
          "the newer release is reported as held back, with the reason")
    check(not any("v2026.10.1" in u for u in f.fetched), "a pre-release is never even looked at")
    check(any("contents/pyproject.toml" in u for u in f.fetched) and
          not any("raw.githubusercontent" in u for u in f.fetched),
          "pyproject comes through the API, where the run's token covers it")
    check(any("gateway.standalone" in w for w in r.watch), "the gateway change is flagged for review")
    check(not any("hermes update" in w for w in r.watch), "...but not the install boilerplate under Updating")
    check(not any("contributor" in w or "co-author" in w for w in r.watch),
          "...nor the contributor paragraph, even where it says 'gateway'")
    check(not any("author field" in w for w in r.watch), "...and 'author' is not 'auth'")
    quoted = [l for l in r.changelog if l and not l.startswith(("**", "完整对比"))]
    check(quoted and all(l.startswith("> ") for l in quoted),
          "upstream notes are quoted, so their headings cannot restructure the PR")
    check(any("`@dev`" in l for l in r.changelog) and any("NousResearch/hermes-agent#126740" in l for l in r.changelog),
          "mentions are defused and issue numbers point upstream")
    check(r.changelog[-1].startswith("完整对比：https://github.com/NousResearch/hermes-agent/compare/v2026.9.21...v2026.9.24"),
          "the compare link is always there, even when upstream's note already has one")

    r = up.check_engine(Fake(), "v2026.10.5", (3, 13, 16))
    check(r.proposed is None, "never proposes going backwards")


def test_an_engine_jump_shows_every_release_in_between():
    """Each engine note covers only its own window.

    Jumping 0.21.3 -> 0.21.5 with only 0.21.5's note would have hidden the
    0.21.4 gateway singleton lock -- the change U-Hermes had to work around.
    """
    r = up.check_engine(Fake(), "v2026.9.14", (3, 13, 16))
    check(r.proposed == "v2026.9.24", "the jump lands on 0.21.5")
    heads = [l for l in r.changelog if l.startswith("**")]
    check(len(heads) == 2 and "0.21.4" in heads[0] and "0.21.5" in heads[1],
          "both 0.21.4 and 0.21.5 are shown, oldest first (%s)" % heads)
    check(any("singleton" in w for w in r.watch), "0.21.4's singleton lock is flagged")
    check(any("local engine updates" in w for w in r.watch), "'updates' counts, not just 'update'")
    long_hit = [w for w in r.watch if "very long clause" in w]
    check(long_hit and long_hit[0].endswith("…") and len(long_hit[0]) < 320,
          "a clause over 300 characters is shortened, not dropped")
    import re as _re
    refs = [w for w in r.watch if "Gateway fixes" in w]
    leftover = _re.sub(r"\[[^\]]*\]\([^)]*\)", "", refs[0]) if refs else "#"
    check(refs and "#" not in leftover and refs[0].count("[") == refs[0].count("]("),
          "...and a long clause full of refs is cut before defusing: no link cut in half, no ref left live")
    check(any("还有" in l for l in r.changelog), "a long note says it was cut, and links the rest")
    check(not any("v2026.10.5" in l for l in r.changelog if l.startswith("**")),
          "a held-back release is not presented as part of the upgrade")


def test_web_ui():
    r = up.check_web_ui(Fake(), "0.7.24")
    check(r.proposed == "0.7.31", "proposes npm's latest")
    text = NL.join(r.changelog)
    check("Android" not in text, "the Android app's release (v1.0.6) is not in the notes")
    check("只在 GitHub" not in text, "a release npm never published is not in them either")
    check("草稿" not in text and "已经钉住" not in text, "drafts and the current version are left out")
    for v in ("0.7.25", "0.7.26", "0.7.30", "0.7.31"):
        check("**%s**" % v in text, "release %s is in the notes" % v)
    watch = NL.join(r.watch)
    check("Gateway 自动启动" in watch, "0.7.26's gateway change is flagged")
    check("默认选择 Ekko" in watch, "0.7.30's default-agent change is flagged")
    check("云端中转" in watch, "0.7.31's cloud relay is flagged")
    check("毛玻璃" not in watch, "a cosmetic change is not")
    check("`@someone`" in text and "@someone " not in text.replace("`@someone`", ""),
          "a mention in a note does not notify anyone from our repo")
    check("EKKOLearnAI/ekko-studio#3290" in text, "#3290 links to the Web UI repo, not to our issue 3290")
    r = up.check_web_ui(Fake(), "0.7.31")
    check(r.proposed is None, "nothing to do when already on latest")

    f = Fake()
    f.npm = {"dist-tags": {"latest": "0.8.0"}, "versions": {"0.7.31": {}, "0.8.0": {}}}
    r = up.check_web_ui(f, "0.7.31")
    check(r.proposed == "0.8.0" and any("0.7 → 0.8" in n for n in r.notes),
          "crossing a minor line is proposed but called out")


def test_quoting_upstream_leaves_no_trace():
    """In a public repo, a link to an upstream PR puts "mentioned this" on it,
    once per bot edit; @name notifies; a bare #N hits our own issue N. GitHub's
    autolinker treats CJK characters as boundaries, so must defuse()."""
    repo = up.WEBUI_REPO
    out = up.defuse("感谢@someone 修复登录问题#123", repo)
    check("`@someone`" in out, "a mention right after Chinese text is defused")
    check("[EKKOLearnAI/ekko-studio#123](https://redirect.github.com/EKKOLearnAI/ekko-studio/issues/123)" in out,
          "#123 right after Chinese text links upstream, through the redirect host")
    out = up.defuse("修复#3290、#3291", repo)
    check(out.count("redirect.github.com") == 2, "both of two adjacent refs are handled")
    out = up.defuse("* fix by @dev in https://github.com/EKKOLearnAI/ekko-studio/pull/3268", repo)
    check("https://redirect.github.com/EKKOLearnAI/ekko-studio/pull/3268" in out
          and "https://github.com/EKKOLearnAI" not in out,
          "a full PR link goes through the redirect host")
    out = up.defuse("see NousResearch/hermes-agent#126740", repo)
    check("(https://redirect.github.com/NousResearch/hermes-agent/issues/126740)" in out,
          "a qualified ref keeps its own repo, through the redirect host")
    check(up.defuse("mail me@example.com", repo) == "mail me@example.com", "an e-mail address is left alone")
    check(up.defuse("run `npm i -g @scope/cli` then", repo) == "run `npm i -g @scope/cli` then",
          "text inside a code span is left alone (backticks there would close the span and make @scope live)")

    # Shortening after defusing once cut a redirect link in half and left
    # the bare ref live again; shorten() cuts at a space, before defusing.
    raw = "Gateway rework " + " ".join("(#%d)" % (106900 + i) for i in range(40))
    out = up.defuse(up.shorten(raw), "NousResearch/hermes-agent")
    import re as _re
    leftover = _re.sub(r"\[[^\]]*\]\([^)]*\)", "", out)
    check(len(up.shorten(raw)) <= 281 and out.endswith("…"), "a long clause is shortened")
    check(up.shorten("abcdefgh " * 50).rstrip("…").split()[-1] == "abcdefgh",
          "...at a word boundary: a cut '#1069' of '#106965' would point at the wrong issue")
    check("#" not in leftover and out.count("[") == out.count("]("),
          "...and every ref in what is left is a whole redirect link, none cut in half")
    check(up.defuse("C# and F#1 are languages", repo) == "C# and F#1 are languages",
          "a # glued to a word is not an issue ref")


def test_every_note_format_is_read_in_full():
    """0.7.24 has only "What's Changed", 31 entries; 0.7.18-0.7.23 use "更新内容".

    The first version read only "更新重点", fell back to the first 10 bullets,
    and lost "enable notification content previews by default" -- the change
    Windows-Start.bat has to counter with STUDIO_PUSH_CONTENT_PREVIEW=0.
    """
    long_note = "## What's Changed" + NL + NL.join(
        "* fix(ui): tidy item %d by @dev in https://github.com/x/y/pull/%d" % (i, i) for i in range(27)) + NL + \
        "* fix(push): enable notification content previews by default (#3131)" + NL + \
        NL.join("* chore %d" % i for i in range(3))
    bilingual = "## 更新内容" + NL + "- 中文条目：网关端口改为 18765" + NL + "## Changes" + NL + "- English entry"
    f = Fake()
    f.npm = {"dist-tags": {"latest": "0.7.24"}, "versions": {"0.7.22": {}, "0.7.23": {}, "0.7.24": {}}}
    f.releases[up.WEBUI_REPO] = [{"tag_name": "v0.7.24", "body": long_note},
                                 {"tag_name": "v0.7.23", "body": bilingual},
                                 {"tag_name": "v0.7.22", "body": "x"}]
    r = up.check_web_ui(f, "0.7.22")
    text = NL.join(r.changelog)
    check("tidy item 24" in text and "tidy item 25" not in text, "25 entries of a long note are shown")
    check("另有 6 条" in text, "...and the rest are counted and linked, not dropped silently")
    check(any("previews by default" in w for w in r.watch),
          "an entry past the shown ones is still read for the watch list")
    check("中文条目" in text and "English entry" not in text,
          "a bilingual note shows its 更新内容 section, not both")
    check(any("18765" in w for w in r.watch), "...and that section is read for the watch list")

    # 0.7.25: the summary is shown, but the full "What's Changed" list is
    # read too -- its "fix group Coding Agent MCP authentication" was not in
    # the summary.
    both = "## 更新重点" + NL + "- 新增 Cursor CLI" + NL + "## What's Changed" + NL + \
        "* [codex] fix group Coding Agent MCP authentication per run by @x in " \
        "https://github.com/EKKOLearnAI/ekko-studio/pull/3170"
    f = Fake()
    f.npm = {"dist-tags": {"latest": "0.7.25"}, "versions": {"0.7.24": {}, "0.7.25": {}}}
    f.releases[up.WEBUI_REPO] = [{"tag_name": "v0.7.25", "body": both}, {"tag_name": "v0.7.24", "body": "x"}]
    r = up.check_web_ui(f, "0.7.24")
    check("Cursor CLI" in NL.join(r.changelog) and "codex" not in NL.join(r.changelog),
          "the curated summary is what is shown")
    check(any("authentication" in w for w in r.watch), "...but every section is read for the watch list")
    check(any(w.endswith("MCP authentication per run") for w in r.watch),
          "...without the 'by @who in <url>' tail GitHub appends to those entries")


def test_releases_are_read_past_the_first_page():
    """ekko-studio also tags Android builds and runtime bundles: 100 releases
    is about three months. A pin older than that must still get its notes."""
    f = Fake()
    filler = lambda n, tag: [{"tag_name": "hermes-0.%d.%d-%s" % (n, i, tag), "body": ""} for i in range(n)]
    rest = WEBUI_RELEASES[1:]
    # Page 1: the newest release and 99 runtime tags. Page 2: exactly 100,
    # with the pinned v0.7.24 among them. Page 3 exists but is not needed.
    f.releases[up.WEBUI_REPO] = ([WEBUI_RELEASES[0]] + filler(99, "a") + rest +
                                 filler(100 - len(rest), "b") + filler(50, "c"))
    r = up.check_web_ui(f, "0.7.24")
    text = NL.join(r.changelog)
    check("**0.7.25**" in text and "**0.7.30**" in text, "versions on page 2 are in the notes")
    check(any("page=2" in u for u in f.fetched), "...because page 2 was read")
    check(not any("page=3" in u for u in f.fetched),
          "...and reading stops once the pin is seen, though page 2 was full")

    keep = up.RELEASE_PAGES
    try:
        up.RELEASE_PAGES = 1
        r = up.check_web_ui(f, "0.7.24")
        check(any("只读了最近" in n for n in r.notices),
              "if the pin is still not reached, the PR says the search stopped -- not that the notes do not exist")
    finally:
        up.RELEASE_PAGES = keep


def test_a_rate_limit_is_not_a_missing_file():
    """Fetcher.exists decides whether a Python patch has an embeddable build.

    A 404 means it does not. A 429 or a 5xx means nobody knows -- read as
    "missing", it would quietly skip a Python version for a week and change
    the offer. It has to surface as a failed lookup instead.
    """
    import urllib.error
    real = up.Fetcher()

    def answering(code):
        def _open(url, method="GET"):
            raise urllib.error.HTTPError(url, code, "x", {}, None)
        return _open

    real._open = answering(404)
    check(real.exists("https://example.invalid/x.zip") is False, "404: the file does not exist")
    for code in (429, 503):
        real._open = answering(code)
        try:
            real.exists("https://example.invalid/x.zip")
            raised = False
        except urllib.error.HTTPError:
            raised = True
        check(raised, "%d: an error, not 'does not exist'" % code)


def test_node():
    r = up.check_node(Fake(), "v24.21.0")
    check(r.proposed == "v24.22.0", "newest v24 LTS that has the Windows zip CI downloads")
    check(any("安全" in n for n in r.notes), "a security release in range is called out")
    check(any("v26" in h for h in r.held), "a newer LTS major is reported, not taken")


def test_uv():
    r = up.check_uv(Fake(), "0.12.16")
    check(r.proposed == "0.12.23", "newest 0.12.x with the Windows asset (0.12.24 has none)")
    check(any("0.13.0" in h for h in r.held), "0.13 is reported, not taken")
    check(any("0.12.20" in w and "Breaking" in w for w in r.watch), "a Breaking changes note is flagged")


def test_python():
    r = up.check_python(Fake(), "3.13.15")
    check(r.proposed == "3.13.16", "newest 3.13 patch that has an embeddable build (3.13.17 has none)")
    check(any("3.14.8" in h for h in r.held), "3.14 is reported, not taken")


def test_rewrite():
    out = up.rewrite(VERSIONS_ENV, {"HERMES_AGENT_REF": "v2026.9.24", "UV_VERSION": "0.12.23"}, "0.21.5")
    check("HERMES_AGENT_REF=v2026.9.24" in out and "UV_VERSION=0.12.23" in out, "values change")
    check("# v2026.9.24 == hermes-agent 0.21.5" in out and "0.21.4" not in out,
          "the comment naming the engine version follows the pin")
    check("# Node comment stays." in out and "# BUMP THIS TOGETHER" in out, "every other comment stays")
    check("NODE_VERSION=v24.21.0" in out, "an unchanged pin is untouched")
    check("\r" not in out, "LF stays LF (setup.sh sources this file)")
    crlf = VERSIONS_ENV.replace(NL, "\r\n")
    check(up.rewrite(crlf, {"UV_VERSION": "0.12.23"}).count("\r\n") == crlf.count("\r\n"),
          "CRLF stays CRLF")
    check(up.rewrite(VERSIONS_ENV, {}) == VERSIONS_ENV, "no changes, byte-identical")


def test_the_engine_is_judged_on_the_python_being_proposed():
    results = up.plan(Fake(), dict(PINS))
    by = {r.key: r for r in results}
    check(by["PYTHON_EMBED_VERSION"].proposed == "3.13.16" and by["HERMES_AGENT_REF"].proposed == "v2026.9.24",
          "the full plan: 3.13.16 + 0.21.5, and 0.22 held back")


def _run_main(tmp, fetcher, extra=()):
    path = os.path.join(tmp, "versions.env")
    body, out, msg = (os.path.join(tmp, n) for n in ("body.md", "out.txt", "commit.txt"))
    for p in (body, out, msg):
        if os.path.exists(p):
            os.remove(p)
    printed = io.StringIO()
    with redirect_stdout(printed):
        up.main(["--write", "--body", body, "--github-output", out, "--commit-msg", msg] + list(extra),
                fetcher=fetcher, versions_path=path)
    read = lambda p: io.open(p, encoding="utf-8", newline="").read()
    return read(path), read(out), read(body), read(msg), printed.getvalue()


def test_main_end_to_end():
    tmp = os.path.join(HERE, "_tmp_update_pins")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        path = os.path.join(tmp, "versions.env")
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(VERSIONS_ENV)
        written, outputs, body, msg, _ = _run_main(tmp, Fake())
        check("HERMES_WEB_UI_VERSION=0.7.31" in written and "PYTHON_EMBED_VERSION=3.13.16" in written,
              "--write rewrites versions.env")
        check(outputs.startswith("changed=true" + NL + "title=跟进上游版本：") and outputs.count(NL) == 2,
              "the workflow gets changed= and title=, one line each")
        check("不会自动合并" in body, "the PR says it never merges")
        check("actions/workflows/release.yml?query=branch%3Aauto%2Fupdate-pins" in body,
              "the PR links the build, which does not show on the PR page")
        for step in ("-Apply", "setup.ps1 -Force", "version_check.py", "Ekko", "8642"):
            check(step in body, "the USB checklist actually installs and checks the new versions (%s)" % step)
        check("HERMES_WEB_UI_VERSION: 0.7.24 -> 0.7.31" in msg, "the commit message lists each move")

        # Run again on the result: upstream has not moved, so nothing does.
        again, outputs, _b, _m, _p = _run_main(tmp, Fake())
        check(outputs.startswith("changed=false" + NL), "a second run with nothing new reports changed=false")
        check(again == written, "...and leaves the file alone")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_failed_lookup_proposes_nothing():
    """A partial answer would change the offer for a reason that has nothing
    to do with upstream: drop a component, or re-offer one a person declined."""
    tmp = os.path.join(HERE, "_tmp_update_pins")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        for outage, what in (("nodejs.org", "Node.js"),
                             ("embed-amd64", "Python"),
                             ("contents/pyproject.toml", "the engine's pyproject"),
                             ("ekko-studio/releases", "the Web UI release notes")):
            with io.open(os.path.join(tmp, "versions.env"), "w", encoding="utf-8", newline="") as fh:
                fh.write(VERSIONS_ENV)
            written, outputs, body, _m, printed = _run_main(tmp, Fake(fail=[outage]))
            check(written == VERSIONS_ENV and outputs.startswith("changed=false" + NL),
                  "%s unreachable: versions.env untouched, changed=false" % what)
            check("查询失败" in body and "::warning::" in printed,
                  "...and the run says which lookup failed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_body_fits_in_a_pull_request():
    r = up.Result("HERMES_WEB_UI_VERSION", "0.7.24")
    r.proposed = r.label = "0.9.0"
    r.changelog = ["- " + "很长的更新说明" * 40] * 2000
    r.notices = ["网页界面：这几个版本在 GitHub 的发布里没有找到说明：0.8.3"]
    _t, body, _c = up.render([r])
    check(len(body) <= 65536, "a huge changelog is cut to fit GitHub's limit (%d chars)" % len(body))
    check("## 这次升级" in body and "setup.ps1 -Force" in body,
          "...keeping the summary and the checklist")
    check("没有找到说明：0.8.3" in body, "...and the notice that some notes were never read")
    check("不会自动合并" in body, "...and the footer: only the release notes are cut")
    check(body.rstrip().endswith("请看各组件的发布页）"), "...which says it was cut")


def test_the_always_kept_part_has_a_budget_too():
    """Only the release notes used to be cut. A watch list long enough to
    fill the head would push the body past GitHub's limit, every PR edit
    would be refused, and nothing would reach the reviewer at all."""
    r = up.Result("HERMES_WEB_UI_VERSION", "0.7.24")
    r.proposed = r.label = "0.9.0"
    r.watch = ["网页界面 0.8.%d：网关默认端口调整，" % i + "说明" * 60 for i in range(2000)]
    r.held = ["AI 引擎 0.%d.0：要求 Python 3.14" % i for i in range(40)]
    _t, body, _c = up.render([r])
    check(len(body) <= 65536, "a huge watch list still fits (%d chars)" % len(body))
    check("列不下了" in body and "setup.ps1 -Force" in body and "不会自动合并" in body,
          "...by cutting the watch list and saying so, keeping the checklist and footer")
    check(body.count("要求 Python 3.14") == up.HELD_SHOWN and "另有 20 个更早的版本" in body,
          "the held list shows the newest and counts the rest")


def test_the_checklist_tests_this_weeks_proposal():
    _t, body, _c = up.render(up.plan(Fake(), dict(PINS)))
    check("git checkout -B auto/update-pins origin/auto/update-pins" in body,
          "step 1 resets to the remote branch: a plain checkout stays on last week's proposal")
    check(".ekko" in body, "step 3 warns that reinstalling clears the Web UI's own Ekko store")


def test_the_issue_helpers():
    title = "跟进上游版本：网页界面 0.7.31"
    bot = {"is_bot": True, "login": "app/github-actions"}   # as gh reports it
    issues = [
        {"number": 1, "title": up.ISSUE_TITLE, "body": "我的问题", "author": {"is_bot": False, "login": "x"}},
        {"number": 4, "title": up.ISSUE_TITLE, "body": "y", "author": {"is_bot": True, "login": "app/dependabot"}},
        {"number": 2, "title": "other", "body": up.OFFER_MARK + title, "author": bot},
        {"number": 3, "title": up.ISSUE_TITLE, "body": "x" + NL + up.OFFER_MARK + title, "author": bot},
    ]
    check(up.bot_issue(issues) == "3", "only an issue the bot opened, under its title, is the bot's")
    check(up.bot_issue(issues, title) == "3", "...and it is found by the set of versions it offered")
    check(up.bot_issue(issues, "跟进上游版本：网页界面 0.7.32") == "", "...but not for a different set")
    check(up.bot_issue(issues[:3]) == "", "a stranger's or another app's issue with the same title is never the bot's")
    body = up.issue_body(title, "PR 正文", "FUDAHA99/U-hermes")
    check("compare/main...auto/update-pins?expand=1&title=%E8%B7%9F%E8%BF%9B" in body,
          "the one-click link carries the exact title, so a PR opened from it can be declined")
    check((up.OFFER_MARK + title) in body.split(NL) and "PR 正文" in body,
          "the issue names its offer on a line of its own and carries the PR text")
    check(up.ISSUE_TITLE in io.open(os.path.join(REPO, "docs", "DEVELOPMENT.md"), encoding="utf-8").read()
          if os.path.isdir(os.path.join(REPO, "docs")) else True,
          "DEVELOPMENT.md names the issue by this title")


def test_the_workflow_text():
    wf_path = os.path.join(REPO, ".github", "workflows", "update-pins.yml")
    if not os.path.isfile(wf_path):
        print("  skip  not a source checkout")
        return
    wf = io.open(wf_path, encoding="utf-8").read()
    check("BRANCH: auto/update-pins" in wf and up.BRANCH == "auto/update-pins",
          "the workflow and the script agree on the branch")
    check("--force-with-lease" in wf and "push --force " not in wf and "push -f " not in wf,
          "a refresh is a lease-checked push, never a blind force")


if __name__ == "__main__":
    for fn in (test_versions_and_specs,
               test_engine_verdict,
               test_engine_picks_the_newest_release_that_runs,
               test_an_engine_jump_shows_every_release_in_between,
               test_web_ui,
               test_quoting_upstream_leaves_no_trace,
               test_every_note_format_is_read_in_full,
               test_releases_are_read_past_the_first_page,
               test_a_rate_limit_is_not_a_missing_file,
               test_node,
               test_uv,
               test_python,
               test_rewrite,
               test_the_engine_is_judged_on_the_python_being_proposed,
               test_main_end_to_end,
               test_a_failed_lookup_proposes_nothing,
               test_body_fits_in_a_pull_request,
               test_the_always_kept_part_has_a_budget_too,
               test_the_checklist_tests_this_weeks_proposal,
               test_the_issue_helpers,
               test_the_workflow_text):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")
