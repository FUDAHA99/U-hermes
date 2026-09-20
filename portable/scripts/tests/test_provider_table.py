"""Every provider button has to line up with the engine's own registry.

Config.html holds three things per provider: the id it writes into
config.yaml, the environment variable it writes the key into, and the
variable it writes the address into.  The engine resolves all three
independently.  When any one of them drifts -- a renamed provider id, a key
variable that provider does not read, an address override that does not
exist -- the user gets a 401 on a key that just passed the connection test,
with nothing on screen to explain it.  That is not something a person can
debug, so it has to be caught here.

Run against the built package's own engine, which is what actually ships:

    portable/hermes/.venv/Scripts/python.exe portable/scripts/tests/test_provider_table.py

It falls back to hermes-agent-ref/ in a checkout that has not been built.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(PORTABLE)
CONFIG_HTML = os.path.join(PORTABLE, "Config.html")

def _engine_label(source):
    """Which engine this run validated against, and whether it is the pinned one.

    Printed on every run because it is the difference between a meaningful
    result and a meaningless one. This suite once reported "all checks
    passed" against a locally installed 0.14.0 while CI checked the 0.21.3
    that ships, and the two disagree about things this file asserts.
    """
    version = "unknown"
    try:
        import importlib.metadata as md
        version = md.version("hermes-agent")
    except Exception:
        pass
    pin = ""
    try:
        with open(os.path.join(PORTABLE, "versions.env"), encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("HERMES_AGENT_REF"):
                    pin = line.split("=", 1)[1].strip()
    except OSError:
        pass
    print("validating against hermes-agent %s from %s (versions.env pins %s)"
          % (version, source, pin or "nothing"))


try:
    from hermes_cli.auth import PROVIDER_REGISTRY
    _engine_label("the installed engine")
except ImportError:
    ref = os.path.join(REPO, "hermes-agent-ref")
    if not os.path.isdir(ref):
        print("SKIP: no hermes engine on sys.path and no hermes-agent-ref/ checkout")
        sys.exit(0)
    sys.path.insert(0, ref)
    from hermes_cli.auth import PROVIDER_REGISTRY
    _engine_label("hermes-agent-ref/, which is NOT what ships")

# Handled outside PROVIDER_REGISTRY by the engine (aggregator /
# user-supplied), so they get explicit expectations below instead of a
# registry lookup. They are NOT skipped: an earlier version let them through
# unchecked, and a mutation test proved the suite passed with OpenRouter's
# key variable set to garbage -- exactly the bug class this file exists for.
OUT_OF_REGISTRY_EXPECTED = {
    # agent/credential_pool.py seeds this pool from OPENROUTER_API_KEY only,
    # and hardcodes OPENROUTER_BASE_URL with no env override.
    "openrouter": {"envVar": "OPENROUTER_API_KEY", "urlVar": ""},
}
NOT_IN_REGISTRY = set(OUT_OF_REGISTRY_EXPECTED) | {"custom"}

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


def parse_buttons(html):
    """The provider buttons, as the page renders them."""
    return {
        m.group(1): {"url": m.group(2), "model": m.group(3)}
        for m in re.finditer(
            r'data-provider="([^"]*)"\s+data-url="([^"]*)"\s+data-model="([^"]*)"', html
        )
    }


def parse_table(html):
    """The builtinProviders map, as saveConfig() uses it."""
    block = re.search(r"const builtinProviders = \{(.*?)\n\s*\};", html, re.S)
    if not block:
        return {}
    entries = {}
    for m in re.finditer(
        r"'([^']+)':\s*\{\s*hermesName:\s*'([^']*)',\s*envVar:\s*'([^']*)',\s*urlVar:\s*'([^']*)'\s*\}",
        block.group(1),
    ):
        entries[m.group(1)] = {
            "hermesName": m.group(2),
            "envVar": m.group(3),
            "urlVar": m.group(4),
        }
    return entries


def main():
    with open(CONFIG_HTML, encoding="utf-8") as f:
        html = f.read()

    buttons = parse_buttons(html)
    table = parse_table(html)
    check(bool(buttons), "the page still has provider buttons this test can read")
    check(bool(table), "the page still has a builtinProviders table this test can read")
    if not buttons or not table:
        return

    for name, entry in sorted(table.items()):
        pid = entry["hermesName"]
        if pid in OUT_OF_REGISTRY_EXPECTED:
            want = OUT_OF_REGISTRY_EXPECTED[pid]
            check(entry["envVar"] == want["envVar"],
                  "%s -> key variable is %s, which is the only one the engine reads (got %s)"
                  % (name, want["envVar"], entry["envVar"]))
            check(entry["urlVar"] == want["urlVar"],
                  "%s -> address override is %s (got %s)"
                  % (name, want["urlVar"] or "(none)", entry["urlVar"] or "(none)"))
            continue
        if pid in NOT_IN_REGISTRY:
            continue

        known = pid in PROVIDER_REGISTRY
        check(known, "%s -> '%s' is a provider the engine knows" % (name, pid))
        if not known:
            continue
        pconfig = PROVIDER_REGISTRY[pid]

        check(
            entry["envVar"] in pconfig.api_key_env_vars,
            "%s -> %s is a key variable '%s' reads (it reads %s)"
            % (name, entry["envVar"], pid, ", ".join(pconfig.api_key_env_vars)),
        )

        check(
            entry["urlVar"] == (pconfig.base_url_env_var or ""),
            "%s -> address override '%s' matches the engine's '%s'"
            % (name, entry["urlVar"] or "(none)", pconfig.base_url_env_var or "(none)"),
        )

        # With no override variable, the engine always calls its own default,
        # so the address the page shows has to already be that address.
        if not pconfig.base_url_env_var and name in buttons:
            shown = buttons[name]["url"].rstrip("/")
            check(
                shown == pconfig.inference_base_url.rstrip("/"),
                "%s has no override variable, so its address must be the engine's "
                "default (page: %s, engine: %s)" % (name, shown, pconfig.inference_base_url),
            )

    # The Kimi button switches to the mainland entry on a moonshot.cn
    # address.  That entry has no override variable, so the address on the
    # button has to be the mainland endpoint exactly.
    cn = table.get("kimi-cn")
    if cn and cn["hermesName"] in PROVIDER_REGISTRY and "kimi" in buttons:
        cn_config = PROVIDER_REGISTRY[cn["hermesName"]]
        shown = buttons["kimi"]["url"].rstrip("/")
        check(
            "moonshot.cn" not in shown or shown == cn_config.inference_base_url.rstrip("/"),
            "the Kimi button's mainland address is the one '%s' calls (page: %s, engine: %s)"
            % (cn["hermesName"], shown, cn_config.inference_base_url),
        )
        check(
            "moonshot.cn" in shown or shown == PROVIDER_REGISTRY["kimi-coding"].inference_base_url.rstrip("/"),
            "a non-mainland Kimi address still resolves through kimi-coding",
        )

    # Every button either resolves through the table or falls through to the
    # custom_providers path, which needs an address to work with.
    custom_key_env = re.search(r'key_env:\s*"(\$\{[^"]*\}|[A-Z_]+)"', html)
    custom_env_var = re.search(r'envContent = `(\$\{?[A-Z_]+\}?|[A-Z_]+)=\$\{apiKey\}', html)
    for name, btn in sorted(buttons.items()):
        if name in table or name == "custom":
            continue
        check(
            bool(btn["url"]),
            "%s is not in builtinProviders, so it needs an address for the "
            "custom-provider path" % name,
        )
        # The custom path only works if the key_env it writes into
        # custom_providers is the same variable it writes into .env.
        check(
            custom_key_env is not None and custom_env_var is not None
            and custom_key_env.group(1) == custom_env_var.group(1),
            "%s goes through the custom path, whose key_env (%s) must match the "
            "variable written to .env (%s)"
            % (name,
               custom_key_env.group(1) if custom_key_env else "?",
               custom_env_var.group(1) if custom_env_var else "?"),
        )

    # The messaging section writes into the same top-level `platforms:` block
    # the gateway's api_server lives in, keyed by the engine's own platform
    # ids. It used to emit a LIST under `gateway:` with invented names
    # ("wechat", "qq"), which nothing read -- no bot ever came up, silently.
    ids = re.search(r"const PLATFORM_IDS = \{(.*?)\};", html, re.S)
    check(ids is not None, "the page still has a PLATFORM_IDS map this test can read")
    if ids:
        try:
            from gateway.config import Platform
            known = {p.value for p in Platform}
        except ImportError:
            known = None
        if known is None:
            print("  skip  no gateway package on sys.path; platform ids unchecked")
        else:
            for label, pid in re.findall(r"(\w+):\s*'([^']+)'", ids.group(1)):
                check(pid in known,
                      "platform '%s' maps to '%s', which the engine knows" % (label, pid))
    check(
        re.search(r"let yaml = 'platforms:\\n'", html) is not None,
        "the messaging section writes a TOP-LEVEL platforms block, not one under gateway:",
    )

    # The workspace is a Windows path, and a Windows path in a double-quoted
    # YAML scalar is read as escape sequences: "C:\temp" parses to C:<TAB>emp
    # with no error at all, and "D:\U-Hermes工作区" does not parse. Single
    # quotes with '' doubling are the only correct form.
    check(
        re.search(r"yamlStr\s*=\s*s\s*=>\s*\"'\"", html) is not None,
        "the workspace path is emitted single-quoted",
    )
    check(
        re.search(r"cwd:\s*\$\{yamlStr\(", html) is not None,
        "terminal.cwd goes through that quoting rather than being interpolated raw",
    )
    check(
        "workspaceKnown" in html and re.search(r"workspaceKnown\s*\?", html) is not None,
        "no terminal: block is written until the current workspace has been read back",
    )
    check(
        re.search(r'id="workspaceDir"[^>]*\bdisabled\b', html) is not None,
        "the workspace box starts disabled, so a failed read cannot blank the setting",
    )


if __name__ == "__main__":
    main()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("provider table: all checks passed")
