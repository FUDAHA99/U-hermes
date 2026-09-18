# -*- coding: utf-8 -*-
"""Apply U-Hermes tweaks to the vendored hermes-agent checkout.

Replaces the old context-diff patches (patches/*.patch), which broke whenever
upstream refactored nearby lines -- as happened with the Sep 2026 cli.py
decomposition. These transforms are anchor-based and idempotent: re-running is
a no-op, and a missing anchor exits non-zero so CI catches upstream drift
instead of shipping a broken package to users.

Usage: python apply-upstream-tweaks.py <path-to-hermes-agent-checkout>
"""
import io
import os
import sys

OK = "[OK]"
SKIP = "[=]"
FAIL = "[X]"


class TweakError(Exception):
    pass


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# --------------------------------------------------------------------------
# Tweak 1: never crash when stdout is not a real console (pythonw / log file)
# --------------------------------------------------------------------------
# Upstream ships a safe helper, _pt_print_ansi(), but still calls the raw
# _pt_print(_PT_ANSI(text)) in the branches that bail out early. On Windows
# without a console those raise NoConsoleScreenBufferError and kill the CLI.
# We route those two sites through the helper.

RAW_CALL = "        _pt_print(_PT_ANSI(text))\n        return\n"
SAFE_CALL = "        _pt_print_ansi(text)\n        return\n"

HELPER_DEF = '''def _pt_print_ansi(text: str) -> None:
    """``_pt_print(ANSI(text))``, falling back to ``print`` when stdout is not a real console."""
    try:
        _pt_print(_PT_ANSI(text))
    except Exception:
        try:
            print(text)
        except Exception:
            pass


'''


def tweak_no_console(agent_dir):
    path = os.path.join(agent_dir, "cli.py")
    if not os.path.exists(path):
        raise TweakError("cli.py not found at %s -- upstream layout changed" % path)

    src = read(path)

    # Upstream added this helper in 2026; inject our own if it ever goes away.
    if "def _pt_print_ansi" not in src:
        anchor = "def _cprint("
        if anchor not in src:
            raise TweakError(
                "cli.py has neither _pt_print_ansi() nor _cprint() -- "
                "upstream refactored the print path; this tweak needs review"
            )
        src = src.replace(anchor, HELPER_DEF + anchor, 1)
        print("  %s cli.py: injected _pt_print_ansi() helper" % OK)

    count = src.count(RAW_CALL)
    if count:
        src = src.replace(RAW_CALL, SAFE_CALL)
        write(path, src)
        print("  %s cli.py: routed %d bail-out print(s) through _pt_print_ansi()" % (OK, count))
    elif "_pt_print_ansi(text)" in src:
        print("  %s cli.py: no-console fallback already in place" % SKIP)
    else:
        raise TweakError(
            "cli.py: found no '_pt_print(_PT_ANSI(text))' + return site and no "
            "existing fallback -- upstream changed the print path; review this tweak"
        )


# --------------------------------------------------------------------------
# Tweak 2: detect the browser language in the Web UI (upstream defaults to en)
# --------------------------------------------------------------------------

LOCALE_ANCHOR = '''  } catch {
    // SSR or privacy mode
  }
  return "en";
}'''

LOCALE_PATCHED = '''  } catch {
    // SSR or privacy mode
  }
  // U-Hermes: auto-detect browser language
  try {
    const langs = navigator.languages || [navigator.language];
    for (const lang of langs) {
      const lower = lang.toLowerCase();
      if (lower.startsWith("zh-tw") || lower.startsWith("zh-hant")) {
        return "zh-hant";
      }
      if (lower.startsWith("zh")) return "zh";
      const prefix = lower.split("-")[0];
      if (isLocale(prefix)) return prefix;
    }
  } catch {
    // ignore
  }
  return "en";
}'''


def tweak_auto_locale(agent_dir):
    path = os.path.join(agent_dir, "web", "src", "i18n", "context.tsx")
    if not os.path.exists(path):
        raise TweakError("web/src/i18n/context.tsx not found -- upstream layout changed")

    src = read(path)

    if "U-Hermes: auto-detect browser language" in src:
        print("  %s context.tsx: auto-locale already applied" % SKIP)
        return

    if "navigator.languages" in src:
        print("  %s context.tsx: upstream now detects browser language itself" % SKIP)
        return

    if LOCALE_ANCHOR not in src:
        raise TweakError(
            "context.tsx: getInitialLocale() no longer matches the expected shape -- "
            "upstream changed locale handling; review this tweak"
        )

    write(path, src.replace(LOCALE_ANCHOR, LOCALE_PATCHED, 1))
    print("  %s context.tsx: browser language auto-detection applied" % OK)


TWEAKS = (
    ("no-console print fallback", tweak_no_console),
    ("Web UI auto locale", tweak_auto_locale),
)


def main():
    if len(sys.argv) != 2:
        print("usage: apply-upstream-tweaks.py <path-to-hermes-agent-checkout>")
        return 2

    agent_dir = os.path.abspath(sys.argv[1])
    if not os.path.isdir(agent_dir):
        print("%s not a directory: %s" % (FAIL, agent_dir))
        return 1

    print("Applying U-Hermes tweaks to %s" % agent_dir)
    failed = []
    for name, fn in TWEAKS:
        try:
            fn(agent_dir)
        except TweakError as e:
            print("  %s %s: %s" % (FAIL, name, e))
            failed.append(name)

    if failed:
        print("")
        print("%s %d tweak(s) could not be applied: %s" % (FAIL, len(failed), ", ".join(failed)))
        print("    Upstream hermes-agent has drifted. Update this script before releasing.")
        return 1

    print("All tweaks applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
