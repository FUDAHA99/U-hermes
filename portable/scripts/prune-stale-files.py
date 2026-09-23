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

Each release ships hermes/manifests/files-<version>.txt listing every file it
put under ROOTS. Extracting over an older install leaves the older list
beside the new one, so on the next start:

    stale = (every older list) - (this version's list)

and only those are deleted. What that deliberately never touches:
  * data\\ and anything else outside ROOTS;
  * any file no release ever shipped (the user's own, or what the engine
    installed at runtime) -- it is in no list;
  * a file this version ships under a different letter case. Windows paths
    are case-insensitive, so deleting Foo.py would delete the new foo.py.

A dev tree (no VERSION, no manifests) is a no-op. Failures never block the
launch: a locked file is reported and the older list kept, so the next start
retries it.

  prune-stale-files.py <install dir>                        (the launcher)
  prune-stale-files.py --write-manifest <dir> <version> [<history dir>]
                                                            (CI, at packaging)
  prune-stale-files.py --check-zip <zip> <version>          (CI, verifying)

Standard library only: the launcher runs this with the bundled base
interpreter, before the venv it may be cleaning up is used.
"""
import io
import os
import sys

ROOTS = ("hermes/.venv/", "hermes/hermes-agent/", "runtime/")
MANIFEST_DIR = "hermes/manifests"
HEADER = "# U-Hermes shipped files"


def manifest_name(version):
    return "files-%s.txt" % version


def _safe(rel):
    """A manifest path we are willing to act on: relative, inside ROOTS, no tricks."""
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or ":" in rel:
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    return rel.startswith(ROOTS)


def read_manifest(path):
    """The set of paths in a manifest, or None if the file is not one of ours."""
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or not lines[0].startswith(HEADER):
        return None
    return {l for l in lines[1:] if l and not l.startswith("#")}


def shipped_files(install_dir):
    """Every file under ROOTS, as manifest paths (forward slashes, on-disk case)."""
    found = []
    for root in ROOTS:
        top = os.path.join(install_dir, *root.rstrip("/").split("/"))
        for dirpath, _dirs, files in os.walk(top):
            for name in files:
                full = os.path.join(dirpath, name)
                found.append(os.path.relpath(full, install_dir).replace(os.sep, "/"))
    return sorted(found)


def write_manifest(install_dir, version, history_dir=None):
    """This release's list, plus the lists of releases that shipped without one.

    Run after everything that adds or removes files under ROOTS: a file
    missing from this list but named by an older one would be deleted from
    a working install. "Verify the packaged artifact" re-checks it against
    the zip itself (check_zip).
    """
    out_dir = os.path.join(install_dir, *MANIFEST_DIR.split("/"))
    os.makedirs(out_dir, exist_ok=True)
    files = shipped_files(install_dir)
    path = os.path.join(out_dir, manifest_name(version))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("%s %s\n" % (HEADER, version))
        for rel in files:
            f.write(rel + "\n")
    if history_dir:
        import gzip
        for name in sorted(os.listdir(history_dir)):
            if not (name.startswith("files-") and name.endswith(".txt.gz")):
                continue
            target = name[:-3]
            if target == manifest_name(version):
                continue  # never let a stored list stand in for the real one
            with open(os.path.join(history_dir, name), "rb") as f:
                data = gzip.decompress(f.read())
            if not data.decode("utf-8").startswith(HEADER):
                raise ValueError("%s is not a manifest" % name)
            with open(os.path.join(out_dir, target), "wb") as f:
                f.write(data)
    return path, len(files)


def check_zip(zip_path, version):
    """Problems with the release zip's own list, as a list of strings (empty = fine).

    The list must name exactly the files under ROOTS in the zip. One it
    misses, that an older list names, would be deleted from every install
    upgraded by extraction.
    """
    import zipfile
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        by_path = {n.replace("\\", "/"): n for n in z.namelist() if not n.endswith(("/", "\\"))}
        mname = MANIFEST_DIR + "/" + manifest_name(version)
        if "VERSION" not in by_path or z.read(by_path["VERSION"]).decode("utf-8").strip() != version:
            problems.append("VERSION in the zip is not %s" % version)
        if mname not in by_path:
            return problems + ["no %s in the zip" % mname]
        text = z.read(by_path[mname]).decode("utf-8").splitlines()
        if not text or not text[0].startswith(HEADER):
            return problems + ["%s has no header" % mname]
        listed = {l for l in text[1:] if l and not l.startswith("#")}
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


def _remove_empty_parents(install_dir, rel):
    """rmdir upward while empty, never above the ROOTS entry the file sat in."""
    root = next(r for r in ROOTS if rel.startswith(r)).rstrip("/")
    parts = rel.split("/")[:-1]
    while len(parts) > len(root.split("/")):
        try:
            os.rmdir(os.path.join(install_dir, *parts))
        except OSError:
            return
        parts.pop()


def prune(install_dir):
    """Returns (deleted, failed, skipped_reason). Never raises for a bad install."""
    try:
        with io.open(os.path.join(install_dir, "VERSION"), encoding="utf-8") as f:
            version = f.read().strip()
    except OSError:
        return 0, [], "no VERSION (not a release install)"
    mdir = os.path.join(install_dir, *MANIFEST_DIR.split("/"))
    try:
        names = [n for n in os.listdir(mdir) if n.startswith("files-") and n.endswith(".txt")]
    except OSError:
        return 0, [], "no manifests"
    current_name = manifest_name(version)
    if current_name not in names:
        # e.g. a pre-manifest release extracted over a newer one: nothing to
        # subtract from, so deleting anything would be a guess.
        return 0, [], "no manifest for %s" % version
    older = [n for n in names if n != current_name]
    if not older:
        return 0, [], None

    keep = read_manifest(os.path.join(mdir, current_name))
    if keep is None:
        return 0, [], "current manifest unreadable"
    keep_folded = {p.casefold() for p in keep}

    stale = set()
    for n in older:
        listed = read_manifest(os.path.join(mdir, n))
        if listed:
            stale |= listed
    stale = {p for p in stale if p.casefold() not in keep_folded and _safe(p)}

    deleted, failed = 0, []
    for rel in sorted(stale):
        path = os.path.join(install_dir, *rel.split("/"))
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            os.remove(path)
            deleted += 1
        except OSError:
            failed.append(rel)
            continue
        _remove_empty_parents(install_dir, rel)

    if not failed:
        for n in older:
            try:
                os.remove(os.path.join(mdir, n))
            except OSError:
                pass
    return deleted, failed, None


def main(argv):
    if len(argv) in (4, 5) and argv[1] == "--write-manifest":
        path, count = write_manifest(os.path.abspath(argv[2]), argv[3],
                                     argv[4] if len(argv) == 5 else None)
        print("wrote %s (%d files)" % (path, count))
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
              " | --write-manifest <dir> <version> [<history dir>]"
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
