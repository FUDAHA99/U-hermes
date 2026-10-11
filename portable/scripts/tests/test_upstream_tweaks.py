"""The tweaks we apply to hermes-agent must follow upstream when it moves code.

In v2026.9.21 upstream moved _cprint() out of cli.py into
hermes_cli/cli_render.py. The two bail-out branches that crash without a real
console moved with it, unchanged -- but the tweak only looked in cli.py, so
every build against the new release failed at "Build portable package".
Failing was the right call (better than shipping unpatched); not following
the move was the bug.

Each test builds a miniature checkout in a temp dir, shaped like the real one
at that release, so this runs with no network and no upstream clone.

Run:  python portable/scripts/tests/test_upstream_tweaks.py
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location(
    "apply_upstream_tweaks", os.path.join(SCRIPTS, "apply-upstream-tweaks.py"))
tweaks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tweaks)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


RAW = "        _pt_print(_PT_ANSI(text))\n        return\n"

# The shape of _cprint at both releases: one branch already safe, two raw.
CPRINT = '''def _cprint(text: str):
    from cli import _PT_ANSI, _pt_print, _pt_print_ansi
    try:
        from prompt_toolkit.application import get_app_or_none
    except Exception:
        _pt_print(_PT_ANSI(text))
        return

    app = get_app_or_none()
    if app is None:
        _pt_print_ansi(text)
        return

    loop = getattr(app, "loop", None)
    if loop is None:
        _pt_print(_PT_ANSI(text))
        return
'''

# v2026.9.24 builds the raw print once, as the paint two branches call, next
# to an already safe paint_fallback -- so the helper's name is in _cprint now
# whether or not anything was patched.
CPRINT_PAINTS = '''def _cprint(text: str):
    from cli import _PT_ANSI, _pt_print, _pt_print_ansi

    def _painted(paint):
        def _paint():
            paint()
        return _paint
    paint_pt = _painted(lambda: _pt_print(_PT_ANSI(text)))
    paint_fallback = _painted(lambda: _pt_print_ansi(text))

    try:
        from prompt_toolkit.application import get_app_or_none, run_in_terminal
    except Exception:
        paint_pt()
        return

    app = get_app_or_none()
    if app is None:
        paint_fallback()
        return

    loop = getattr(app, "loop", None)
    if loop is None:
        paint_pt()
        return
    with suppress(Exception):
        run_in_terminal(paint_pt)
'''

# What v2026.9.14-v2026.9.21 also have: the raw print handed to
# run_in_terminal under suppress(), on purpose. Not a bail-out.
GUARDED = '''
    with suppress(Exception):
        coro = run_in_terminal(lambda: _pt_print(_PT_ANSI(text)))
'''

# A shape no anchor knows, beside a call of the helper that makes it look done.
CPRINT_DRIFTED = '''def _cprint(text: str):
    from cli import _PT_ANSI, _pt_print, _pt_print_ansi
    raw = (lambda: _pt_print(_PT_ANSI(text)))
    try:
        from prompt_toolkit.application import get_app_or_none
    except Exception:
        raw()
        return
    _pt_print_ansi(text)
'''

# The realistic next drift: paint_fallback kept, the raw paint reshaped.
CPRINT_RESHAPED = CPRINT_PAINTS.replace(
    "paint_pt = _painted(lambda: _pt_print(_PT_ANSI(text)))",
    "rendered = _PT_ANSI(text)" + chr(10) + "    paint_pt = _painted(lambda: _pt_print(rendered))")

# No helper anywhere and no shape we know: the injected helper would go unused.
CPRINT_NO_HELPER = '''def _cprint(text: str):
    from cli import _PT_ANSI, _pt_print
    try:
        from prompt_toolkit.application import get_app_or_none
    except Exception:
        emit = _pt_print
        emit(_PT_ANSI(text))
        return
'''

HELPER = '''def _pt_print_ansi(text: str) -> None:
    from cli import _PT_ANSI, _pt_print
    try:
        _pt_print(_PT_ANSI(text))
    except Exception:
        print(text)


'''

LOCALE = '''function getInitialLocale() {
  try {
    const saved = localStorage.getItem("locale");
  } catch {
    // SSR or privacy mode
  }
  return "en";
}
'''


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path


def read(root, rel):
    with io.open(os.path.join(root, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


def checkout(layout):
    """A miniature hermes-agent tree as it looked at a given release."""
    root = tempfile.mkdtemp(prefix="tweaks-")
    write(root, "web/src/i18n/context.tsx", LOCALE)
    if layout == "v2026.9.14":
        write(root, "cli.py", "import os\n\n\n" + HELPER + CPRINT)
    elif layout == "no-helper":
        write(root, "cli.py", "import os" + chr(10) * 3 + CPRINT_NO_HELPER)
    elif layout in ("v2026.9.24", "drifted", "guarded", "reshaped"):
        body = {"v2026.9.24": CPRINT_PAINTS, "drifted": CPRINT_DRIFTED, "guarded": CPRINT + GUARDED,
                "reshaped": CPRINT_RESHAPED}[layout]
        write(root, "cli.py",
              "from hermes_cli.cli_render import (  # noqa: F401\n"
              "    _cprint,\n    _pt_print_ansi,\n)\n")
        write(root, "hermes_cli/__init__.py", "")
        write(root, "hermes_cli/cli_render.py", "from contextlib import suppress\n\n\n" + HELPER + body)
    elif layout == "v2026.9.21":
        # cli.py re-exports the helpers; worktree_ops has a delegating _cprint
        # of its own, which must not be mistaken for the real one.
        write(root, "cli.py",
              "from hermes_cli.cli_render import (  # noqa: F401\n"
              "    _cprint,\n    _pt_print_ansi,\n)\n")
        write(root, "hermes_cli/__init__.py", "")
        write(root, "hermes_cli/cli_render.py", "from contextlib import suppress\n\n\n" + HELPER + CPRINT)
        write(root, "hermes_cli/worktree_ops.py",
              "def _cprint(text: str) -> None:\n"
              "    from cli import _cprint as _impl\n"
              "    _impl(text)\n")
    elif layout == "gone":
        write(root, "cli.py", "print('no _cprint anywhere')\n")
        write(root, "hermes_cli/cli_render.py", "def render():\n    pass\n")
    return root


def run_tweak(root):
    """(exit code, printed output) of the whole script against a checkout."""
    out = io.StringIO()
    argv = sys.argv
    sys.argv = ["apply-upstream-tweaks.py", root]
    try:
        with contextlib.redirect_stdout(out):
            code = tweaks.main()
    finally:
        sys.argv = argv
    return code, out.getvalue()


def test_the_old_layout_still_gets_patched():
    print("v2026.9.14: _cprint lives in cli.py")
    root = checkout("v2026.9.14")
    try:
        code, out = run_tweak(root)
        src = read(root, "cli.py")
        check(code == 0, "the tweak succeeds")
        check(src.count(RAW) == 0, "no raw bail-out print is left in cli.py")
        check(src.count("_pt_print_ansi(text)\n        return") == 3,
              "all three branches now go through the safe helper")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_new_layout_is_followed():
    print("v2026.9.21: _cprint moved to hermes_cli/cli_render.py")
    root = checkout("v2026.9.21")
    try:
        cli_before = read(root, "cli.py")
        ops_before = read(root, "hermes_cli/worktree_ops.py")
        code, out = run_tweak(root)
        src = read(root, "hermes_cli/cli_render.py")
        check(code == 0, "the tweak succeeds (it failed the build before this fix)")
        check(src.count(RAW) == 0, "no raw bail-out print is left in cli_render.py")
        check(src.count("def _pt_print_ansi") == 1,
              "upstream's own helper is used, not a second copy injected")
        check(read(root, "cli.py") == cli_before, "cli.py, now just re-exports, is left alone")
        check(read(root, "hermes_cli/worktree_ops.py") == ops_before,
              "worktree_ops' delegating _cprint is not mistaken for the real one")
        check("cli_render.py" in out, "the log names the file it actually patched")

        code, out = run_tweak(root)
        check(code == 0 and read(root, "hermes_cli/cli_render.py") == src,
              "running it twice changes nothing")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_without_a_console_the_patched_print_does_not_crash():
    print("the point of the tweak: no console must not mean no CLI")
    for layout in ("v2026.9.21", "v2026.9.24"):
        crashed, printed = print_without_console(layout, patch=True)
        check(crashed is None, "%s: no exception escapes _cprint (got %r)" % (layout, crashed))
        check("你好" in printed, "%s: the text still reaches stdout as plain print" % layout)
    # The fixture really takes the raw branch: unpatched, it crashes.
    crashed, _ = print_without_console("v2026.9.24", patch=False)
    check(crashed is not None, "(unpatched v2026.9.24 does crash there, so the check above means something)")


def print_without_console(layout, patch):
    root = checkout(layout)
    saved = {k: sys.modules.get(k) for k in ("cli", "prompt_toolkit.application", "cli_render_under_test")}
    try:
        if patch:
            run_tweak(root)
        spec = importlib.util.spec_from_file_location(
            "cli_render_under_test", os.path.join(root, "hermes_cli", "cli_render.py"))
        render = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(render)

        class NoConsoleScreenBufferError(Exception):
            pass

        def pt_print(_):
            raise NoConsoleScreenBufferError("stdout is a log file")

        fake_cli = types.ModuleType("cli")
        fake_cli._PT_ANSI = lambda text: text
        fake_cli._pt_print = pt_print
        fake_cli._pt_print_ansi = render._pt_print_ansi
        sys.modules["cli"] = fake_cli
        # The first raw branch: prompt_toolkit's application module won't import.
        sys.modules["prompt_toolkit.application"] = None

        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                render._cprint("你好")
            crashed = None
        except Exception as e:  # what the unpatched code does
            crashed = e
        return crashed, out.getvalue()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        shutil.rmtree(root, ignore_errors=True)


def test_the_paint_layout_is_patched():
    print("v2026.9.24: the raw print is a paint, built once")
    root = checkout("v2026.9.24")
    try:
        code, out = run_tweak(root)
        src = read(root, "hermes_cli/cli_render.py")
        check(code == 0, "the tweak succeeds")
        check(tweaks.SAFE_PAINT in src and tweaks.RAW_PAINT not in src,
              "the paint both bail-outs call goes through the safe helper")
        check(tweaks.raw_prints_outside_helper(src) == 0, "no raw print is left outside the helper")
        check("routed 1" in out, "the log says it patched something (it said 'already in place' before)")
        code, _ = run_tweak(root)
        check(code == 0 and read(root, "hermes_cli/cli_render.py") == src, "running it twice changes nothing")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_raw_print_in_a_new_shape_stops_the_build():
    print("a raw print no anchor knows: fail, even though the helper's name is there")
    root = checkout("drifted")
    try:
        before = read(root, "hermes_cli/cli_render.py")
        code, out = run_tweak(root)
        check(code == 1, "the tweak exits 1 instead of claiming the fallback is in place")
        check("already in place" not in out, "and does not say it is")
        check(read(root, "hermes_cli/cli_render.py") == before, "the file is left as it was")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_reshaped_raw_paint_stops_the_build():
    print("v2026.9.24 with its raw paint reshaped: no anchor matches, the end state still fails")
    for layout, label in (("reshaped", "_pt_print(rendered) next to an already safe paint_fallback"),
                          ("no-helper", "no helper upstream and nothing to route: ours would go unused")):
        root = checkout(layout)
        try:
            rel = "cli.py" if layout == "no-helper" else "hermes_cli/cli_render.py"
            before = read(root, rel)
            code, out = run_tweak(root)
            check(code == 1, "%s: the tweak exits 1" % label)
            check("already in place" not in out and read(root, rel) == before,
                  "%s: claims nothing, changes nothing" % label)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_the_guarded_run_in_terminal_print_is_not_a_bail_out():
    print("v2026.9.14-v2026.9.21 hand the raw print to run_in_terminal under suppress()")
    root = checkout("guarded")
    try:
        code, _ = run_tweak(root)
        src = read(root, "hermes_cli/cli_render.py")
        check(code == 0, "the tweak succeeds")
        check("run_in_terminal(lambda: _pt_print(_PT_ANSI(text)))" in src, "and leaves that call as it is")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_if_upstream_moves_it_again_the_build_still_stops():
    print("_cprint nowhere we know: fail loudly, never ship unpatched")
    root = checkout("gone")
    try:
        code, out = run_tweak(root)
        check(code == 1, "the tweak exits 1, so CI stops at Build portable package")
        check("_cprint" in out, "the log says what it was looking for")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_the_old_layout_still_gets_patched()
    test_the_new_layout_is_followed()
    test_without_a_console_the_patched_print_does_not_crash()
    test_the_paint_layout_is_patched()
    test_a_raw_print_in_a_new_shape_stops_the_build()
    test_the_guarded_run_in_terminal_print_is_not_a_bail_out()
    test_a_reshaped_raw_paint_stops_the_build()
    test_if_upstream_moves_it_again_the_build_still_stops()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")
