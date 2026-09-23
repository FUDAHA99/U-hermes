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
    root = checkout("v2026.9.21")
    saved = {k: sys.modules.get(k) for k in ("cli", "prompt_toolkit.application", "cli_render_under_test")}
    try:
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
        check(crashed is None, "no exception escapes _cprint (got %r)" % (crashed,))
        check("你好" in out.getvalue(), "the text still reaches stdout as plain print")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
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
    test_if_upstream_moves_it_again_the_build_still_stops()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("all checks passed")
