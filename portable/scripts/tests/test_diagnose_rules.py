"""Every rule in diagnose.py's ERROR_CLASSES must fire on a real log line.

A regex that never matches is worse than no rule at all: the line it was
meant to explain falls through to "另有 N 行错误不属于已知类型", and whoever
wrote the rule believes the case is covered.  So each rule here is pinned to
the log lines it is supposed to catch, assembled from the format strings in
hermes-agent v2026.9.14 -- the version that ships -- plus one line copied
verbatim out of a real data/logs/errors.log.

The last test is the one that matters most: it fails if ANY rule has no
sample, so a new rule cannot be added without a line proving it works.

Run:  python portable/scripts/tests/test_diagnose_rules.py
"""
import importlib.util
import os
import re
import sys

# CI pipes this suite's stdout, and Python then encodes it with the machine's
# ANSI codepage rather than UTF-8. On GitHub's en-US Windows runner that is
# cp1252, which cannot encode a single Chinese character, so the first label
# containing one killed the whole release job with a UnicodeEncodeError.
# Unreproducible on a Chinese Windows box, where the codepage is GBK.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

_spec = importlib.util.spec_from_file_location(
    "diagnose", os.path.join(SCRIPTS, "diagnose.py")
)
dg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dg)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


# --- Real lines, as hermes_logging.py's _LOG_FORMAT renders them ------------
# "%(asctime)s %(levelname)s%(session_tag)s %(name)s: %(message)s"

WAL_DELETE_OVERRIDDEN = (
    "2026-09-21 09:12:03,117 ERROR hermes_state: state.db: "
    "database.journal_mode=delete is configured but the on-disk database is "
    "already WAL; keeping WAL (a live downgrade under open connections can "
    "corrupt the DB). To apply journal_mode=DELETE, stop all connections to "
    "this DB and run a one-time offline 'PRAGMA journal_mode=DELETE' on the "
    "file. This message fires once per process per database."
)
WAL_DELETE_OVERRIDDEN_OTHER_DB = WAL_DELETE_OVERRIDDEN.replace(
    "state.db:", "response_store.db:"
)
WAL_CANNOT_VERIFY = (
    "2026-09-21 09:12:03,120 ERROR hermes_state: could not verify journal "
    "mode before applying configured journal_mode=delete (database is locked "
    "— possible concurrent openers); refusing to downgrade a database "
    "this process does not exclusively own"
)

# One prefix, three outcomes. The engine appends one of
# _WAL_RESET_BUG_ACTIONS (hermes_state_wal.py @ v2026.9.14, quoted verbatim),
# and only the first of them means the danger was actually dealt with.
def _wal_reset_line(action):
    return (
        # 3.49.1 measured on the aligned install, not taken on trust: the
        # engine's own is_sqlite_wal_reset_vulnerable() returns True for it,
        # so this warning really does fire on the shipped build.
        "2026-09-21 09:12:03,050 WARNING hermes_state: state.db: linked SQLite "
        "3.49.1 (interpreter 3.13.15) is vulnerable to the WAL-reset corruption "
        "bug (https://sqlite.org/wal.html#walresetbug) — " + action +
        ". Upgrade to SQLite 3.51.3+ (or backports 3.50.7 / 3.44.6); see "
        "`hermes doctor`. This warning fires once per process per database."
    )


# Handled: the engine switched away from WAL. Nothing for the user to do.
WAL_RESET_BUG = _wal_reset_line(
    "using journal_mode=DELETE instead of enabling WAL")
# NOT handled: the database is still in WAL on a build the engine calls
# corruption-prone. This is what an upgraded install actually hits.
WAL_RESET_KEPT_WAL = _wal_reset_line(
    "is already in WAL mode — leaving WAL in place "
    "(no live downgrade under concurrent openers)")
WAL_RESET_INDETERMINATE = _wal_reset_line(
    "journal mode could not be verified or exclusively switched (database is "
    "locked — possible concurrent openers); leaving the journal mode "
    "untouched (no live downgrade under concurrent openers)")

KEY_MISSING = (
    "2026-09-21 09:12:04,001 ERROR gateway.platforms.api_server: [Api_Server] "
    "Refusing to start: API_SERVER_KEY is required for the API server, "
    "including loopback-only binds on 127.0.0.1."
)
KEY_TOO_SHORT = (
    "2026-09-21 09:12:04,002 ERROR gateway.platforms.api_server: [Api_Server] "
    "Refusing to start: API_SERVER_KEY is a placeholder or too short (<16 "
    "chars). This endpoint dispatches terminal-capable agent work — a "
    "guessable key is remote code execution."
)
KEY_UNVERIFIABLE = (
    "2026-09-21 09:12:04,003 ERROR gateway.platforms.api_server: [Api_Server] "
    "Refusing to start: API_SERVER_KEY strength could not be verified "
    "(ValueError: bad), and this endpoint dispatches terminal-capable agent "
    "work. Repair the installation before starting the API server on 127.0.0.1."
)
KEY_REJECTED = (
    "2026-09-21 09:12:05,002 WARNING gateway.platforms.api_server: API server "
    "rejected invalid API key: remote='127.0.0.1' method='POST' "
    "path='/v1/responses'"
)
KEY_NO_PROFILE = (
    "2026-09-21 09:12:05,003 WARNING gateway.platforms.api_server: API server "
    "rejected request for profile 'work': no profile-scoped API_SERVER_KEY is "
    "configured; source='unknown'"
)
# Copied verbatim from a real portable/data/logs/errors.log written by the
# older engine, which accepted every request instead of refusing to start.
KEY_LEGACY_NONE = (
    "2026-05-19 23:30:59,439 WARNING gateway.platforms.api_server: "
    "[Api_Server] ⚠️  No API key configured (API_SERVER_KEY / "
    "platforms.api_server.key). All requests will be accepted without "
    "authentication. Set an API key for production deployments to prevent "
    "unauthorized access to sessions, responses, and cron jobs."
)

# Lines the older rules own; the new ones must keep their hands off.
#
# These predate this file. Their strings are taken from each rule's own
# literal alternation rather than re-read out of the engine, so they prove
# the regex matches what its author meant -- not that the engine still
# emits it. The three at the bottom had no sample at all until the
# dead-rule invariant below went in.
OTHER_RULES = [
    "2026-09-21 10:00:00,000 ERROR agent: error_type=AuthenticationError HTTP 401 Invalid API Key",
    "2026-09-21 10:00:01,000 ERROR agent: error_type=APITimeoutError Request timed out",
    "2026-09-21 10:00:02,000 ERROR agent: error_type=RateLimitError HTTP 429",
    "2026-09-21 10:00:03,000 ERROR gateway: [Errno 10048] Port 8642 already in use",
    "2026-09-21 10:00:04,000 ERROR agent: error_type=NotFoundError HTTP 404",
    "2026-09-21 10:00:05,000 ERROR agent: No inference provider configured",
    "2026-09-21 10:00:09,000 ERROR agent: error_type=APIConnectionError Connection error.",
    "2026-09-21 10:00:10,000 ERROR agent: Stream stale for 45s, no chunks received",
    "2026-09-21 10:00:11,000 ERROR gateway: spawn EINVAL",
    "2026-09-21 10:00:12,000 ERROR gateway: Failed to canonicalize script path",
]

# Ordinary chatter that must match nothing at all.
NOISE = [
    "2026-09-21 10:00:06,000 WARNING agent: session 12 resumed from snapshot",
    "2026-09-21 10:00:07,000 WARNING hermes_state: vacuum reclaimed 12 MB",
    "2026-09-21 10:00:08,000 WARNING gateway: telegram poll returned 0 updates",
]

ALL_SAMPLES = [
    WAL_DELETE_OVERRIDDEN, WAL_DELETE_OVERRIDDEN_OTHER_DB, WAL_CANNOT_VERIFY,
    WAL_RESET_BUG, WAL_RESET_KEPT_WAL, WAL_RESET_INDETERMINATE,
    KEY_MISSING, KEY_TOO_SHORT, KEY_UNVERIFIABLE,
    KEY_REJECTED, KEY_NO_PROFILE, KEY_LEGACY_NONE,
] + OTHER_RULES


def rule(fragment):
    """The one rule whose pattern contains this fragment."""
    hits = [r for r in dg.ERROR_CLASSES if fragment in r[0]]
    assert len(hits) == 1, "expected exactly one rule containing %r, got %d" % (
        fragment, len(hits))
    return hits[0]


def matches(r, line):
    return re.compile(r[0]).search(line) is not None


def test_every_pattern_compiles():
    for pattern, meaning, advice in dg.ERROR_CLASSES:
        try:
            re.compile(pattern)
            ok = True
        except re.error:
            ok = False
        check(ok, "compiles: %s" % pattern[:56])
        check(bool(meaning.strip()) and bool(advice.strip()),
              "has both an explanation and an action: %s" % pattern[:40])


def test_wal_rule():
    r = rule("journal_mode=delete is configured")
    check(matches(r, WAL_DELETE_OVERRIDDEN), "catches the WAL-kept-on-purpose error")
    check(matches(r, WAL_DELETE_OVERRIDDEN_OTHER_DB),
          "...for any database, not just state.db")
    check(matches(r, WAL_CANNOT_VERIFY), "catches the locked-database variant")
    for other in (WAL_RESET_BUG, WAL_RESET_KEPT_WAL, WAL_RESET_INDETERMINATE):
        check(not matches(r, other),
              "does NOT swallow a SQLite-version warning")
    for line in OTHER_RULES + NOISE:
        check(not matches(r, line), "no false hit on: %s" % line[40:80])


def test_api_server_key_rule():
    r = rule("Refusing to start: API_SERVER_KEY")
    for name, line in (("missing", KEY_MISSING), ("too short", KEY_TOO_SHORT),
                       ("unverifiable", KEY_UNVERIFIABLE),
                       ("rejected request", KEY_REJECTED),
                       ("no profile key", KEY_NO_PROFILE),
                       ("older engine, no key at all", KEY_LEGACY_NONE)):
        check(matches(r, line), "catches the gateway key case: %s" % name)
    for line in [WAL_DELETE_OVERRIDDEN, WAL_RESET_BUG] + OTHER_RULES + NOISE:
        check(not matches(r, line), "no false hit on: %s" % line[40:80])


def test_the_sqlite_warning_is_split_by_outcome():
    """Calling all three outcomes benign is worse than having no rule.

    The two "still in WAL" variants are exactly the state the quick-start's
    offline-conversion procedure exists for, and an upgraded install hits
    them: its preserved config.yaml has no `database:` block, so the
    delete-was-overridden rule never fires and this line is the only thing
    the user is shown.
    """
    benign = rule("using journal_mode=DELETE instead of enabling WAL")
    check(matches(benign, WAL_RESET_BUG), "the handled outcome is recognised")
    check("不是故障" in benign[1], "...and is plainly labelled not-a-fault")
    check(not matches(benign, WAL_RESET_KEPT_WAL),
          "a database left IN WAL is not called benign")
    check(not matches(benign, WAL_RESET_INDETERMINATE),
          "an unverifiable journal mode is not called benign either")

    act = rule("is already in WAL mode")
    check(matches(act, WAL_RESET_KEPT_WAL), "still-in-WAL is reported as actionable")
    check(matches(act, WAL_RESET_INDETERMINATE), "so is could-not-verify")
    check(not matches(act, WAL_RESET_BUG),
          "...but the handled outcome is not nagged about")
    check("第八节" in act[2] and "安全弹出" in act[2],
          "the advice points at the conversion procedure and the eject warning")

    for r in (benign, act):
        for line in [WAL_DELETE_OVERRIDDEN, KEY_MISSING] + OTHER_RULES + NOISE:
            check(not matches(r, line), "no false hit on: %s" % line[40:80])


def test_no_rule_is_dead():
    """The invariant: a rule with no sample here is a rule nobody has seen fire."""
    for pattern, _meaning, _advice in dg.ERROR_CLASSES:
        rx = re.compile(pattern)
        check(any(rx.search(line) for line in ALL_SAMPLES),
              "at least one sample line exercises: %s" % pattern[:56])


def test_noise_stays_unclassified():
    for line in NOISE:
        hit = [p for p, _m, _a in dg.ERROR_CLASSES if re.compile(p).search(line)]
        check(not hit, "ordinary log chatter matches no rule: %s" % line[40:80])


if __name__ == "__main__":
    for fn in (test_every_pattern_compiles, test_wal_rule,
               test_api_server_key_rule, test_the_sqlite_warning_is_split_by_outcome,
               test_no_rule_is_dead, test_noise_stays_unclassified):
        print(fn.__name__)
        fn()
    print()
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("diagnose rules: all checks passed")
