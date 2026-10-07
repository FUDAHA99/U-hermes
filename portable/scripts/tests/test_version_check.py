# -*- coding: utf-8 -*-
"""The check that exists because three bugs came from not having it.

Each of these was verified against a version nobody ships, on a machine
where every pinned component had drifted and nothing said so:

  * engine conclusions drawn from 0.14.0 while 0.21.3 ships;
  * the Web UI account claim, a no-op on the pinned 0.7.22;
  * the test mock for that feature, written to match the local 0.6.5, which
    is why the suite agreed with the bug.

So the two failure modes that matter here are opposites, and both are
tested: staying quiet when versions really differ, and crying drift when a
component simply cannot be read -- the second would fail every CI build,
which is the fastest way to get a check deleted.

Run:  python portable/scripts/tests/test_version_check.py
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location(
    "version_check", os.path.join(SCRIPTS, "version_check.py"))
vc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vc)

FAILURES = []
NL = chr(10)


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


def fake_tree(pins):
    root = os.path.join(HERE, "_tmp_versions")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    with io.open(os.path.join(root, "versions.env"), "w", encoding="utf-8") as f:
        f.write("# a comment that must be ignored" + NL)
        for k, v in pins.items():
            f.write("%s=%s%s" % (k, v, NL))
    return root


def with_readers(**installed):
    """Swap the component readers for fixed answers."""
    original = vc.COMPONENTS
    vc.COMPONENTS = tuple(
        (label, key, (lambda v: (lambda _p=None: v))(installed.get(key, vc.UNKNOWN)))
        for label, key, _ in original)
    return original


def test_pins_are_parsed():
    root = fake_tree({"NODE_VERSION": "v24.21.0", "UV_VERSION": "0.12.16"})
    pins = vc.read_pins(root)
    check(pins.get("NODE_VERSION") == "v24.21.0", "a pin is read")
    check("#" not in "".join(pins.keys()), "comments are skipped")
    shutil.rmtree(root, ignore_errors=True)


def test_the_v_prefix_is_not_a_difference():
    check(vc._same("v24.21.0", "v24.21.0") is True, "identical matches")
    check(vc._same("v24.21.0", "24.21.0") is True, "a missing v is not drift")
    check(vc._same("3.13.15", "3.11.9") is False, "a real difference is drift")
    check(vc._same("v2026.9.14", "v2026.5.16-12-gabc") is False,
          "a describe suffix is drift -- the checkout is not on the tag")


def test_unreadable_is_not_drift():
    """The one that would get this check deleted.

    A component that is simply absent -- a release zip with no .git, a mac
    build inspected on Windows -- must read as "cannot tell". Reporting it
    as a mismatch fails every build for the one case where the pin is
    guaranteed right.
    """
    check(vc._same("v24.21.0", vc.UNKNOWN) is None, "absent is unknown")
    check(vc._same("", "v24.21.0") is None, "no pin is unknown")

    original = with_readers()  # every reader answers UNKNOWN
    try:
        root = fake_tree({"NODE_VERSION": "v24.21.0"})
        rows = vc.survey(root)
        check(vc.drifted(rows) == [], "nothing readable means nothing drifted")
        check(all(r[4] is None for r in rows), "...every verdict is unknown")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        vc.COMPONENTS = original


def test_a_bare_sha_means_cannot_tell():
    """A shallow clone can describe as a bare SHA. That is not a mismatch."""
    root = os.path.join(HERE, "_tmp_sha", "hermes", "hermes-agent", ".git")
    shutil.rmtree(os.path.join(HERE, "_tmp_sha"), ignore_errors=True)
    os.makedirs(root)
    portable = os.path.join(HERE, "_tmp_sha")
    real_run = vc._run
    try:
        vc._run = lambda *a, **k: "a1b2c3d4e5f6"
        check(vc.engine_version(portable) is vc.UNKNOWN,
              "a bare SHA reads as unknown, not as the wrong version")
        vc._run = lambda *a, **k: "v2026.9.14"
        check(vc.engine_version(portable) == "v2026.9.14", "a tag is reported")
        vc._run = lambda *a, **k: "v2026.5.16-12-gabcdef0"
        check(vc.engine_version(portable) == "v2026.5.16-12-gabcdef0",
              "a describe suffix is reported, so it can be judged drift")
    finally:
        vc._run = real_run
        shutil.rmtree(os.path.join(HERE, "_tmp_sha"), ignore_errors=True)


def test_drift_is_reported_and_named():
    original = with_readers(
        HERMES_AGENT_REF="v2026.5.16",
        HERMES_WEB_UI_VERSION="0.6.5",
        NODE_VERSION="v24.21.0",
    )
    try:
        root = fake_tree({
            "HERMES_AGENT_REF": "v2026.9.14",
            "HERMES_WEB_UI_VERSION": "0.7.22",
            "NODE_VERSION": "v24.21.0",
        })
        rows = vc.survey(root)
        bad = vc.drifted(rows)
        check(len(bad) == 2, "both mismatches are found, and only those")
        keys = {r[1] for r in bad}
        check(keys == {"HERMES_AGENT_REF", "HERMES_WEB_UI_VERSION"},
              "the right two components are named")

        lines = []
        count = vc.report(rows, say=lambda *ls: lines.extend(ls))
        text = NL.join(lines)
        check(count == 2, "report counts them")
        check("0.6.5" in text and "0.7.22" in text,
              "the message shows installed AND pinned, not just that they differ")
        check("v2026.5.16" in text and "v2026.9.14" in text,
              "...for every drifted component")
        check("v24.21.0" not in text, "a matching component is not mentioned")
        check("setup.ps1" in text or "Menu" in text,
              "the message says how to fix it")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        vc.COMPONENTS = original


def test_everything_matching_says_nothing():
    original = with_readers(NODE_VERSION="v24.21.0", UV_VERSION="0.12.16")
    try:
        root = fake_tree({"NODE_VERSION": "v24.21.0", "UV_VERSION": "0.12.16"})
        rows = vc.survey(root)
        check(vc.drifted(rows) == [], "a matching install has no drift")
        lines = []
        check(vc.report(rows, say=lambda *ls: lines.extend(ls)) == 0,
              "...and the report is silent")
        check(lines == [], "...completely silent, so it is not noise")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        vc.COMPONENTS = original


def test_the_python_pin_only_applies_where_a_python_is_bundled():
    """PYTHON_EMBED_VERSION is the Windows *embeddable* interpreter.

    setup.sh downloads no Python at all -- it runs `uv venv --python 3.11`
    and takes what it gets. Comparing that against this pin made the macOS
    CI job fail on drift the pin never claimed to govern. A check that
    fails for a reason nobody intends to fix is a check that gets deleted,
    so where nothing is bundled it compares nothing and says so.
    """
    root = os.path.join(HERE, "_tmp_nopy")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, "runtime"))
    check(vc.python_version(root) is vc.UNKNOWN,
          "no bundled interpreter -> nothing to compare")

    # ...and it is not silently skipped forever: the moment a build does
    # bundle one, the pin is enforced again.
    os.makedirs(os.path.join(root, "runtime", "python-win-x64"))
    real_run = vc._run
    try:
        vc._run = lambda *a, **k: "3.13.15"
        os.makedirs(os.path.join(root, "hermes", ".venv", "Scripts"))
        io.open(os.path.join(root, "hermes", ".venv", "Scripts", "python.exe"),
                "w").close()
        check(vc.python_version(root) == "3.13.15",
              "a bundled interpreter is read and compared")
    finally:
        vc._run = real_run
        shutil.rmtree(root, ignore_errors=True)


def test_every_pinned_key_is_actually_checked():
    """A pin nothing reads is a pin nothing enforces."""
    pins = set(vc.read_pins())
    checked = {key for _l, key, _r in vc.COMPONENTS}
    missing = pins - checked
    check(not missing,
          "every key in versions.env has a reader (unchecked: %s)"
          % ", ".join(sorted(missing)))


def _main_quietly(argv, root):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = vc.main(argv, portable=root)
    return rc, out.getvalue()


CANARY_DRIFT = dict(
    HERMES_AGENT_REF="v2026.9.24-37-g781334eea",
    HERMES_WEB_UI_VERSION="0.7.31",
    NODE_VERSION="v24.21.0",
)
PINS = {
    "HERMES_AGENT_REF": "v2026.9.21",
    "HERMES_WEB_UI_VERSION": "0.7.24",
    "NODE_VERSION": "v24.21.0",
}
CANARY_ARGS = ["--unpinned", "HERMES_AGENT_REF",
               "--unpinned", "HERMES_WEB_UI_VERSION"]


def test_without_unpinned_drift_fails_the_build():
    """The release path: unchanged, any drift is exit 3."""
    original = with_readers(**CANARY_DRIFT)
    try:
        root = fake_tree(PINS)
        rc, _ = _main_quietly(["--quiet"], root)
        check(rc == 3, "drift with no --unpinned still fails (exit 3)")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        vc.COMPONENTS = original


def test_unpinned_components_show_but_do_not_fail():
    """The canary builds upstream's latest engine and Web UI on purpose.

    Failing on that expected difference made the canary red every week,
    which is the same as having no canary: the 2026-10-07 run that actually
    caught upstream going 3.14-only looked like every other red week.
    """
    original = with_readers(**CANARY_DRIFT)
    try:
        root = fake_tree(PINS)
        rc, out = _main_quietly(CANARY_ARGS, root)
        check(rc == 0, "the two components a canary unpins do not fail it")
        check("0.7.31" in out and "0.7.24" in out,
              "...but the table still shows what was built against what")
        check(out.count("本次不钉") == 2,
              "...and marks exactly those two rows as deliberately unpinned")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        vc.COMPONENTS = original


def test_a_pinned_component_still_fails_beside_unpinned_ones():
    """Node, Python and uv still come from the pins on a canary build.

    If setup ever slipped back to @latest for one of those, the canary is
    the run most likely to show it, so --unpinned must not become a
    blanket pass.
    """
    original = with_readers(**dict(CANARY_DRIFT, NODE_VERSION="v25.0.0"))
    try:
        root = fake_tree(PINS)
        rc, out = _main_quietly(CANARY_ARGS, root)
        check(rc == 3, "Node drift fails a canary build that unpins only the other two")
        check("不一致" in out, "...and is marked as a mismatch")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        vc.COMPONENTS = original


def test_a_misspelt_unpinned_key_is_refused():
    """A typo would exempt nothing and look like it worked."""
    original = with_readers(**CANARY_DRIFT)
    try:
        root = fake_tree(PINS)
        rc, out = _main_quietly(["--unpinned", "HERMES_WEBUI_VERSION"], root)
        check(rc == 2, "an unknown key is a usage error (exit 2), not a silent no-op")
        check("HERMES_WEB_UI_VERSION" in out, "...and the message lists the real keys")
        rc, _ = _main_quietly(["--unpinned"], root)
        check(rc == 2, "--unpinned with nothing after it is refused too")
        shutil.rmtree(root, ignore_errors=True)
    finally:
        vc.COMPONENTS = original


def test_ci_unpins_only_what_the_canary_leaves_unpinned():
    """release.yml must not grow into "unpin everything".

    The canary swaps exactly two things for upstream's latest: the engine
    clone and the Web UI npm spec. Anything else passed to --unpinned would
    hide real drift on every canary run.
    """
    repo = os.path.dirname(os.path.dirname(SCRIPTS))
    if not (os.path.isfile(os.path.join(repo, ".gitattributes"))
            and os.path.isdir(os.path.join(repo, ".github"))):
        # Shipped in the release zip too; there is no workflow to read there.
        print("  skip  not a source checkout")
        return
    lines = io.open(os.path.join(repo, ".github", "workflows", "release.yml"),
                    encoding="utf-8").read().split(NL)
    # The calls themselves -- not py_compile, not the zip file list.
    call = re.compile(r'(\$py|"\$PY")\s+scripts/version_check\.py\b(.*)$')
    calls = [(i, m.group(2)) for i, l in enumerate(lines)
             for m in [call.search(l)] if m]
    canary = [(i, rest) for i, rest in calls if "--unpinned" in rest]
    release = [(i, rest) for i, rest in calls if "--unpinned" not in rest]

    check(len(canary) == 2, "each job passes --unpinned on its canary call (found %d)"
          % len(canary))
    want = {"HERMES_AGENT_REF", "HERMES_WEB_UI_VERSION"}
    for i, rest in canary:
        keys = set(re.findall(r"--unpinned\s+([A-Z_]+)", rest))
        check(keys == want, "line %d unpins exactly the engine and the Web UI (found: %s)"
              % (i + 1, ", ".join(sorted(keys))))
        guard = NL.join(lines[max(0, i - 2):i])
        check("CANARY" in guard, "line %d is only reached on a canary run" % (i + 1))
    check(len(release) == 2, "each job still has a call that enforces every pin (found %d)"
          % len(release))
    stray = [i + 1 for i, l in enumerate(lines)
             if "--unpinned" in l and not call.search(l)]
    check(not stray, "--unpinned appears nowhere but on those calls (lines: %s)"
          % ", ".join(map(str, stray)))

    # The flip side of letting a canary through: it must never publish. A
    # canary dispatched on a tag ref used to reach the upload step, and the
    # version check was only an accidental barrier in front of it -- one
    # that held when npm's latest Web UI differed from the pin, and was
    # gone the moment they matched.
    uploads = [i for i, l in enumerate(lines) if "name: Upload to Release" in l]
    check(len(uploads) == 2, "both jobs have an upload step (found %d)" % len(uploads))
    for i in uploads:
        cond = next((l for l in lines[i + 1:i + 6] if l.strip().startswith("if:")), "")
        check("env.CANARY != 'true'" in cond,
              "the upload step at line %d is skipped on canary runs" % (i + 1))


if __name__ == "__main__":
    for fn in (test_pins_are_parsed,
               test_the_v_prefix_is_not_a_difference,
               test_unreadable_is_not_drift,
               test_a_bare_sha_means_cannot_tell,
               test_drift_is_reported_and_named,
               test_everything_matching_says_nothing,
               test_the_python_pin_only_applies_where_a_python_is_bundled,
               test_every_pinned_key_is_actually_checked,
               test_without_unpinned_drift_fails_the_build,
               test_unpinned_components_show_but_do_not_fail,
               test_a_pinned_component_still_fails_beside_unpinned_ones,
               test_a_misspelt_unpinned_key_is_refused,
               test_ci_unpins_only_what_the_canary_leaves_unpinned):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("version check: all checks passed")
