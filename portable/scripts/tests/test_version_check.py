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
import importlib.util
import io
import os
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


if __name__ == "__main__":
    for fn in (test_pins_are_parsed,
               test_the_v_prefix_is_not_a_difference,
               test_unreadable_is_not_drift,
               test_a_bare_sha_means_cannot_tell,
               test_drift_is_reported_and_named,
               test_everything_matching_says_nothing,
               test_the_python_pin_only_applies_where_a_python_is_bundled,
               test_every_pinned_key_is_actually_checked):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("version check: all checks passed")
