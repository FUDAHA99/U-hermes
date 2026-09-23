# -*- coding: utf-8 -*-
"""Delete program files an older release shipped and this one does not.

The upgrade the guide recommends is "extract the new zip over the old folder,
replace files". Extraction replaces files but never deletes one, so every
engine upgrade leaves the previous engine's removed modules behind -- and the
engine imports whatever it finds in tools/, providers/ and plugin folders.
v0.4.3 -> v0.4.4 left 508 files, among them a second hermes_agent dist-info
(importlib.metadata then reported 0.21.3 for a 0.21.4 engine), a tool
upstream had deleted, and a provider plugin upstream had deleted because its
relay was dead. Harmless that time; the pile only grows.

Each release zip carries hermes/manifests/files-<version>.txt: every file it
puts under ROOTS, built from the zip's own entries. Extracting over an older
install leaves that release's list beside the new one, so on the next start

    stale = (lists of OLDER releases found here) - (this release's list)

is deleted. The cost is lopsided -- a file left behind is the old status
quo, a needed file deleted is a broken install -- so every doubt resolves
to "keep":

  * only ROOTS, never data\\ or anything else, and only files some release
    listed: the user's own files are in no list;
  * inside the venv, only the OLD ENGINE's own files: those named by the
    RECORD of a hermes_agent dist-info that is going away (plus their
    __pycache__). The venv is shared -- the engine installs optional
    packages into it at runtime, with dependencies on what we ship -- and
    two rounds of review showed path-based pruning of third-party packages
    breaking them (a dependency deleted under a runtime install; a runtime
    install with a shipped name deleted outright), with the engine still
    believing them installed. Stale third-party files are left, as they
    always were: nothing imports them by scanning. Every stale file that
    mattered in v0.4.3 -> v0.4.4 (28 of them) was the engine's own. A file
    any other installed distribution claims is kept regardless;
  * letter case is ignored (Windows would delete this release's foo.py for
    a stale Foo.py), and a path whose real location differs from its name
    -- a junction on the way, an 8.3 alias -- is skipped;
  * a list is only applied if its version is older than this one: an
    extraction interrupted before VERSION was replaced must not delete the
    new files already written;
  * a list is only trusted if its trailer count matches: a torn write reads
    as "unreadable", not as a short list;
  * lists stored for releases that shipped without one (legacy-files-*) are
    applied at most once per install: the first run leaves a marker that no
    zip contains, so re-extracting any later release cannot bring them back
    into play;
  * anything that cannot be checked or deleted keeps the older list, so the
    next start retries.

A dev tree (no VERSION, no manifests) is a no-op, and nothing here ever
blocks the launch.

  prune-stale-files.py <install dir>                         (the launcher)
  prune-stale-files.py --add-to-zip <zip> <version> [<history dir>]
                                                             (CI, packaging)
  prune-stale-files.py --check-zip <zip> <version>           (CI, verifying)

Standard library only: the launcher runs this with the bundled base
interpreter, before the venv it may be cleaning up is used.
"""
import csv
import io
import os
import posixpath
import re
import sys

ROOTS = ("hermes/.venv/", "hermes/hermes-agent/", "runtime/")
SITE = "hermes/.venv/Lib/site-packages/"
MANIFEST_DIR = "hermes/manifests"
HEADER = "# U-Hermes shipped files"
TRAILER = "# end"
LEGACY_PREFIX = "legacy-"
LEGACY_DONE = ".legacy-applied"   # written by the first run; in no zip
ENGINE_DIST = re.compile(r"^hermes_agent-[^/]+\.dist-info$", re.I)


def manifest_name(version):
    return "files-%s.txt" % version


def parse_version(v):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _safe(rel):
    """A manifest path we are willing to act on: relative, inside ROOTS, no tricks."""
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or ":" in rel:
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts):
        return False
    return rel.startswith(ROOTS)


def render_manifest(version, paths):
    paths = sorted(paths)
    return "%s %s\n%s%s %d\n" % (HEADER, version, "".join(p + "\n" for p in paths), TRAILER, len(paths))


def parse_manifest(text):
    """The set of paths in a manifest, or None unless it is whole and ours."""
    lines = text.splitlines()
    if len(lines) < 2 or not lines[0].startswith(HEADER):
        return None
    m = re.match(r"^%s (\d+)$" % re.escape(TRAILER), lines[-1])
    body = [l for l in lines[1:-1] if l]
    if not m or int(m.group(1)) != len(body):
        return None
    return set(body)


def read_manifest(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            return parse_manifest(f.read())
    except (OSError, UnicodeDecodeError):
        return None


# --- file-system helpers that do not lie about long paths ------------------

def _ext(path):
    """Extended-length form on Windows, so >260-char paths are seen as they are."""
    path = os.path.abspath(path)
    if os.name != "nt" or path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):  # \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _plain(path):
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def _is_redirect(path):
    path = _ext(path)
    return os.path.islink(path) or getattr(os.path, "isjunction", lambda _p: False)(path)


def _real(path):
    return os.path.normcase(_plain(os.path.realpath(_ext(path))))


def _resolves_to_itself(install_dir, base_real, rel):
    """False when a junction, symlink or alias (8.3 name, trailing dot) inside
    the install makes the name point somewhere else than it says. Compared
    against the install's own real location, so an install that itself sits
    behind a junction or a subst drive is not mistaken for one."""
    try:
        real = _real(os.path.join(install_dir, *rel.split("/")))
    except OSError:
        return False
    return real == os.path.normcase(os.path.join(base_real, *rel.split("/")))


# --- what must survive ------------------------------------------------------

def _record_paths(site, dist):
    """Casefolded manifest paths a dist-info's RECORD names (raises on failure)."""
    with io.open(_ext(os.path.join(site, dist, "RECORD")), encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return {posixpath.normpath(SITE + row[0].replace("\\", "/")).casefold()
            for row in rows if row and row[0]}


def scan_distributions(install_dir, going):
    """(engine_owned, protected, unreadable) for the venv.

    engine_owned: what the RECORD of a going hermes_agent dist-info names --
    the only venv files prune may delete. A dist-info is going when its
    RECORD is in `going` (an older release shipped it, this one does not).
    protected: what every other dist-info on disk claims -- this release's,
    the engine's runtime installs, the user's -- which wins over the above.
    unreadable: going engine dist-infos whose RECORD could not be read; their
    files are kept and the older list with them, so the next start retries.
    A site-packages that cannot be listed yields nothing to delete.
    """
    site = os.path.join(install_dir, *SITE.rstrip("/").split("/"))
    engine_owned, protected, unreadable = set(), set(), []
    try:
        names = os.listdir(_ext(site))
    except OSError:
        return engine_owned, protected, unreadable
    for d in names:
        if not d.endswith(".dist-info"):
            continue
        is_going = (SITE + d + "/RECORD").casefold() in going
        if is_going and not ENGINE_DIST.match(d):
            continue  # a third-party package an older release shipped: left alone
        try:
            paths = _record_paths(site, d)
        except (OSError, UnicodeDecodeError, csv.Error):
            if is_going:
                unreadable.append(SITE + d)
            continue
        (engine_owned if is_going else protected).update(paths)
    return engine_owned, protected, unreadable


def _deletable_in_venv(rel_folded, engine_owned):
    """An old-engine file, or the bytecode cache of one."""
    if rel_folded in engine_owned:
        return True
    if "/__pycache__/" in rel_folded and rel_folded.endswith(".pyc"):
        folder, name = rel_folded.rsplit("/__pycache__/", 1)
        return (folder + "/" + name.split(".")[0] + ".py") in engine_owned
    return False


def _remove_empty_parents(install_dir, rel):
    """rmdir upward while empty, never at or above the ROOTS entry the file sat in."""
    root = next(r for r in ROOTS if rel.startswith(r)).rstrip("/")
    parts = rel.split("/")[:-1]
    while len(parts) > len(root.split("/")):
        d = os.path.join(install_dir, *parts)
        if _is_redirect(d):
            return
        try:
            os.rmdir(_ext(d))
        except OSError:
            return
        parts.pop()


# --- the launcher's job -------------------------------------------------------

def prune(install_dir):
    """Returns (deleted, failed, reason). Never raises for a bad install."""
    try:
        with io.open(os.path.join(install_dir, "VERSION"), encoding="utf-8") as f:
            version = f.read().strip()
    except (OSError, UnicodeDecodeError):
        return 0, [], "no VERSION (not a release install)"
    current_v = parse_version(version)
    mdir = os.path.join(install_dir, *MANIFEST_DIR.split("/"))
    try:
        names = os.listdir(mdir)
    except OSError:
        return 0, [], "no manifests"
    if current_v is None or manifest_name(version) not in names:
        # A pre-manifest release extracted over a newer one, or a build with
        # no release number: nothing trustworthy to subtract from.
        return 0, [], "no usable list for %s" % version
    real, legacy = [], []
    for n in names:
        m = re.match(r"^(%s)?files-(.+)\.txt$" % re.escape(LEGACY_PREFIX), n)
        if not m or n == manifest_name(version):
            continue
        v = parse_version(m.group(2))
        if v is None or v >= current_v:
            continue  # newer: an interrupted extraction, or a downgrade -- never guess
        (legacy if m.group(1) else real).append(n)
    # Legacy lists stand in for releases that shipped without one. Every zip
    # carries them, so they are applied at most once per install: the first
    # run leaves a marker no zip contains, and after that they are only
    # cleared away -- whether this is an upgrade, a re-extraction of the same
    # release, or a downgrade.
    marker = os.path.join(mdir, LEGACY_DONE)
    legacy_done = os.path.exists(marker)
    applied = real + ([] if legacy_done else legacy)
    if not applied:
        if legacy or not legacy_done:
            _finish(mdir, legacy, marker)
        return 0, [], None  # every later start: one listdir, one exists()
    keep = read_manifest(os.path.join(mdir, manifest_name(version)))
    if keep is None:
        return 0, [], "this release's list is unreadable"

    listed = set()
    unreadable_lists = []
    for n in applied:
        got = read_manifest(os.path.join(mdir, n))
        if got is None:
            unreadable_lists.append(n)
        else:
            listed |= got
    keep_folded = {p.casefold() for p in keep}
    stale = {p for p in listed if p.casefold() not in keep_folded and _safe(p)}
    going = {p.casefold() for p in stale}
    engine_owned, protected, failed = scan_distributions(install_dir, going)
    base_real = _real(install_dir)

    deleted = 0
    for rel in sorted(stale):
        folded = rel.casefold()
        if folded in protected:
            continue
        if rel.startswith("hermes/.venv/") and not _deletable_in_venv(folded, engine_owned):
            continue
        path = os.path.join(install_dir, *rel.split("/"))
        try:
            os.lstat(_ext(path))
        except FileNotFoundError:
            continue
        except OSError:
            failed.append(rel)
            continue
        if (not os.path.isfile(_ext(path)) or _is_redirect(path)
                or not _resolves_to_itself(install_dir, base_real, rel)):
            continue
        try:
            os.remove(_ext(path))
        except FileNotFoundError:
            continue  # another launch got there first
        except OSError:
            failed.append(rel)
            continue
        deleted += 1
        _remove_empty_parents(install_dir, rel)

    if not failed:
        _finish(mdir, real + legacy + unreadable_lists, marker)
    return deleted, failed, None


def _finish(mdir, lists, marker):
    """Drop lists that are done with, and note that legacy lists have had their turn."""
    for n in lists:
        try:
            os.remove(os.path.join(mdir, n))
        except OSError:
            pass
    try:
        with open(marker, "a"):
            pass
    except OSError:
        pass


# --- CI -----------------------------------------------------------------------

def add_to_zip(zip_path, version, history_dir=None):
    """Append this release's list -- built from the zip's own entries -- and the
    stored lists of releases that shipped without one.

    Built from the zip rather than the build folder, because the two differ:
    Compress-Archive skips hidden items, and the git clone's .git is hidden.
    A list naming a file the zip lacks is harmless; a zip file the list
    misses would be deleted from every install upgraded by extraction.
    """
    import gzip
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        entries = {n.replace("\\", "/") for n in z.namelist() if not n.endswith(("/", "\\"))}
    ours = sorted(p for p in entries if p.startswith(ROOTS))
    bad = [p for p in ours if not _safe(p)]
    if bad:
        raise ValueError("zip entries a list must not carry: %s" % bad[:3])
    added = {MANIFEST_DIR + "/" + manifest_name(version): render_manifest(version, ours)}
    if history_dir:
        for name in sorted(os.listdir(history_dir)):
            m = re.match(r"^files-(.+)\.txt\.gz$", name)
            if not m or m.group(1) == version:
                continue
            with open(os.path.join(history_dir, name), "rb") as f:
                text = gzip.decompress(f.read()).decode("utf-8")
            if parse_manifest(text) is None:
                raise ValueError("%s is not a whole manifest" % name)
            added[MANIFEST_DIR + "/" + LEGACY_PREFIX + name[:-3]] = text
    clash = [n for n in added if n in entries]
    if clash:
        raise ValueError("the zip already has %s" % clash)
    with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
        for name, text in added.items():
            z.writestr(name, text.encode("utf-8"))
    return len(ours), sorted(added)


def check_zip(zip_path, version):
    """Problems with the release zip's own list, as a list of strings (empty = fine)."""
    import zipfile
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        by_path = {n.replace("\\", "/"): n for n in z.namelist() if not n.endswith(("/", "\\"))}
        mname = MANIFEST_DIR + "/" + manifest_name(version)
        if "VERSION" not in by_path or z.read(by_path["VERSION"]).decode("utf-8").strip() != version:
            problems.append("VERSION in the zip is not %s" % version)
        if mname not in by_path:
            return problems + ["no %s in the zip" % mname]
        listed = parse_manifest(z.read(by_path[mname]).decode("utf-8"))
        if listed is None:
            return problems + ["%s is not a whole manifest" % mname]
        actual = {p for p in by_path if p.startswith(ROOTS)}
        missing = sorted(actual - listed)
        extra = sorted(listed - actual)
        if missing:
            problems.append("%d file(s) in the zip are not in its list, e.g. %s" % (len(missing), missing[:3]))
        if extra:
            problems.append("%d listed file(s) are not in the zip, e.g. %s" % (len(extra), extra[:3]))
        unsafe = [p for p in listed if not _safe(p)]
        if unsafe:
            problems.append("unsafe entries in the list, e.g. %s" % unsafe[:3])
    return problems


def main(argv):
    if len(argv) in (4, 5) and argv[1] == "--add-to-zip":
        count, names = add_to_zip(argv[2], argv[3], argv[4] if len(argv) == 5 else None)
        print("added to %s: %s (%d files listed)" % (argv[2], ", ".join(names), count))
        return 0
    if len(argv) == 4 and argv[1] == "--check-zip":
        problems = check_zip(argv[2], argv[3])
        for p in problems:
            print("manifest check: " + p)
        if not problems:
            print("manifest check: the list matches the zip")
        return 1 if problems else 0
    if len(argv) != 2:
        print("usage: prune-stale-files.py <install dir>"
              " | --add-to-zip <zip> <version> [<history dir>]"
              " | --check-zip <zip> <version>")
        return 2
    try:
        deleted, failed, _reason = prune(os.path.abspath(argv[1]))
    except Exception as e:  # never block the launch over housekeeping
        print("  [!] 清理旧版本文件时出错，已跳过：%s" % e)
        return 0
    if deleted:
        print("  [OK] 清理了旧版本留下的 %d 个程序文件。" % deleted)
    if failed:
        print("  [!] 有 %d 个旧版本文件暂时删不掉（可能正被占用），下次启动会再试。" % len(failed))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
