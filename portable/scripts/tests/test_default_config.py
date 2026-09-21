"""Six places write the first config.yaml. They must all write the same one.

This has already gone wrong once. Commit 1b16ad5 ("three disagreeing default
configs") found setup.ps1 shipping a shape the engine reads differently: a
`providers:` block it never reads, `api_server:` at the top level instead of
under `platforms:` so the gateway got no port and no key, `skills.extra_dirs`
instead of `external_dirs` so the bundled Chinese skills never loaded, a
`gateway.platforms` list nothing reads, and no `database.journal_mode`. That
commit fixed the copy it was looking at and missed two more -- and the one in
setup.sh ran *before* the launcher's config.yaml.default copy, so on mac and
Linux the broken one won.

Comparing the parsed mappings, rather than the text, is deliberate: comments
and key order differ between a PowerShell here-string, a bash heredoc and a
batch echo block, and none of that matters. What matters is what the engine
loads.

Run:  python portable/scripts/tests/test_default_config.py
"""
import io
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(PORTABLE)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


def read(*parts):
    return io.open(os.path.join(*parts), encoding="utf-8-sig").read()


def dedent(text):
    lines = [l for l in text.split(chr(10))]
    widths = [len(l) - len(l.lstrip(" ")) for l in lines if l.strip()]
    pad = min(widths) if widths else 0
    return chr(10).join(l[pad:] if l.strip() else "" for l in lines)


def between(text, start, end, after=0):
    """The text between two markers, searching from `after`."""
    i = text.index(start, after)
    j = text.index(end, i + len(start))
    return text[i + len(start):j], j


def producers():
    """(label, yaml text) for every place a first config.yaml comes from."""
    out = []

    # 1 + 2. Both CI jobs write data/config.yaml.default -- the Windows one
    #        from a PowerShell here-string, the mac one from a heredoc.
    wf = read(REPO, ".github", "workflows", "release.yml")
    body, _ = between(wf, '@"' + chr(10),
                      '"@ | Set-Content "data/config.yaml.default"')
    out.append(("release.yml (windows job)", dedent(body)))
    body, _ = between(wf, "cat > data/config.yaml.default << 'CFGEOF'" + chr(10),
                      chr(10) + "          CFGEOF")
    out.append(("release.yml (mac job)", dedent(body)))

    # 3. setup.ps1, for anyone building from source on Windows.
    ps = read(PORTABLE, "setup.ps1")
    body, _ = between(ps, '$defaultConfig = @"' + chr(10), chr(10) + '"@')
    out.append(("setup.ps1", dedent(body)))

    # 4. Windows-Start.bat's fallback, used when the zip has no default.
    bat = read(PORTABLE, "Windows-Start.bat")
    body, _ = between(bat, "        echo model:",
                      ') > "%DATA_DIR%' + chr(92) + 'config.yaml"')
    lines = []
    for line in ("        echo model:" + body).split(chr(10)):
        stripped = line.strip()
        if not stripped.startswith("echo"):
            continue
        lines.append(stripped[len("echo "):] if stripped != "echo." else "")
    out.append(("Windows-Start.bat", chr(10).join(lines) + chr(10)))

    # 5. setup.sh, for anyone building from source on mac or Linux.
    sh = read(PORTABLE, "setup.sh")
    body, _ = between(sh, "<< 'EOF'" + chr(10), chr(10) + "EOF" + chr(10))
    out.append(("setup.sh", body + chr(10)))

    # 6. Mac-Start.command's fallback.
    mac = read(PORTABLE, "Mac-Start.command")
    body, _ = between(mac, "<< 'CFGEOF'" + chr(10), chr(10) + "CFGEOF" + chr(10))
    out.append(("Mac-Start.command", body + chr(10)))

    return out


def test_all_six_parse():
    import yaml
    for label, text in producers():
        try:
            data = yaml.safe_load(text)
            ok = isinstance(data, dict)
        except Exception as exc:
            ok = False
            print("       %s: %s" % (label, exc.__class__.__name__))
        check(ok, "%s produces a YAML mapping" % label)


def test_all_six_agree():
    import yaml
    parsed = []
    for label, text in producers():
        try:
            parsed.append((label, yaml.safe_load(text)))
        except Exception:
            parsed.append((label, None))
    ref_label, ref = parsed[0]
    for label, data in parsed[1:]:
        if data == ref:
            check(True, "%s matches %s" % (label, ref_label))
            continue
        keys = set(ref or {}) ^ set(data or {})
        diff = sorted(keys) or [
            k for k in (ref or {}) if (ref or {}).get(k) != (data or {}).get(k)]
        check(False, "%s differs from %s on: %s" % (label, ref_label, ", ".join(diff)))


def test_the_shape_is_the_one_the_engine_reads():
    """Pin the specific keys the 1b16ad5 investigation established."""
    import yaml
    label, text = producers()[0]
    cfg = yaml.safe_load(text)

    check(isinstance(cfg.get("platforms"), dict)
          and isinstance(cfg["platforms"].get("api_server"), dict),
          "the gateway is under platforms.api_server, not at the top level")
    check("api_server" not in cfg,
          "...and there is no top-level api_server: the engine ignores")
    extra = (cfg.get("platforms", {}).get("api_server", {}) or {}).get("extra")
    check(isinstance(extra, dict) and extra.get("port") == 8642,
          "the port is under api_server.extra, where the engine reads it")
    check(isinstance(cfg.get("skills"), dict)
          and "external_dirs" in cfg["skills"],
          "skills.external_dirs -- extra_dirs is read by nothing upstream")
    check("extra_dirs" not in (cfg.get("skills") or {}),
          "...and the dead spelling is gone")
    check((cfg.get("database") or {}).get("journal_mode") == "delete",
          "database.journal_mode is set to delete")
    check("providers" not in cfg,
          "no providers: block -- the engine reads custom_providers/model instead")
    check("platforms" not in (cfg.get("gateway") or {}),
          "no gateway.platforms list, which nothing reads")
    check("minimax.chat" not in text,
          "the dead api.minimax.chat endpoint is not reintroduced")
    check(str(cfg.get("model", {}).get("provider", "x")) == "",
          "provider is blank, which is what sends the user to the config page")


if __name__ == "__main__":
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("SKIP: PyYAML not available in this interpreter")
        sys.exit(0)
    for fn in (test_all_six_parse, test_all_six_agree,
               test_the_shape_is_the_one_the_engine_reads):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("default config: all checks passed")
