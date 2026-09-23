"""Cleaning up after "extract the new zip over the old one" must only ever
remove files an older release shipped and this one does not.

The cost of getting this wrong is not symmetric. A stale file left behind is
what we have today; a current file deleted, or anything under data\\, is a
broken install or lost chats. Most of the cases below are about what must
survive.

Run:  python portable/scripts/tests/test_prune_stale_files.py
"""
import gzip
import importlib.util
import io
import os
import shutil
import stat
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "prune_stale_files", os.path.join(SCRIPTS, "prune-stale-files.py"))
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

FAILURES = []


def check(condition, label):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAILURES.append(label)


SITE = "hermes/.venv/Lib/site-packages/"


def put(root, rel, text="x"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def exists(root, rel):
    return os.path.exists(os.path.join(root, *rel.split("/")))


def manifest(root, version, paths, header=True):
    lines = (["%s %s" % (ps.HEADER, version)] if header else []) + list(paths)
    put(root, "%s/%s" % (ps.MANIFEST_DIR, ps.manifest_name(version)), "\n".join(lines) + "\n")


def install(version="v0.4.5"):
    root = tempfile.mkdtemp(prefix="prune-")
    put(root, "VERSION", version + "\n")
    return root


def quiet(fn, *a):
    out = io.StringIO()
    saved, sys.stdout = sys.stdout, out
    try:
        return fn(*a)
    finally:
        sys.stdout = saved


def test_what_v0_4_4_left_behind_is_removed():
    print("the real case: v0.4.4 extracted over v0.4.3")
    root = install("v0.4.4")
    try:
        old_meta = SITE + "hermes_agent-0.21.3.dist-info/METADATA"
        old_tool = SITE + "tools/setup_mcp_tool.py"
        old_plugin = SITE + "plugins/model-providers/opencode-free/__init__.py"
        new_meta = SITE + "hermes_agent-0.21.4.dist-info/METADATA"
        shared = SITE + "tools/registry.py"
        for rel in (old_meta, old_tool, old_plugin, new_meta, shared):
            put(root, rel)
        manifest(root, "v0.4.3", [old_meta, old_tool, old_plugin, shared])
        manifest(root, "v0.4.4", [new_meta, shared])

        deleted, failed, _ = ps.prune(root)
        check(deleted == 3 and not failed, "three stale files deleted (%d, %r)" % (deleted, failed))
        check(not exists(root, old_meta), "the second dist-info is gone, so metadata reports 0.21.4 again")
        check(not exists(root, SITE + "hermes_agent-0.21.3.dist-info"), "...and its now-empty folder")
        check(not exists(root, old_tool), "the tool upstream deleted is not auto-imported any more")
        check(not exists(root, SITE + "plugins/model-providers/opencode-free"),
              "the deleted provider plugin's folder is gone")
        check(exists(root, new_meta) and exists(root, shared), "everything this version ships is still there")
        check(exists(root, SITE + "tools"), "tools/ itself stays: it still holds a current file")
        check(not exists(root, ps.MANIFEST_DIR + "/files-v0.4.3.txt"),
              "the older list is dropped once it is fully applied")
        check(exists(root, ps.MANIFEST_DIR + "/files-v0.4.4.txt"), "this version's list stays")
        check(ps.prune(root)[:2] == (0, []), "the next start is a no-op")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_nothing_the_user_owns_is_touched():
    print("data\\, the user's own files and runtime installs survive")
    root = install()
    try:
        in_data = "data/state.db"
        outside_roots = "scripts/old_helper.py"
        user_file = SITE + "someone_pip_installed.py"
        for rel in (in_data, outside_roots, user_file):
            put(root, rel)
        # An older list that (wrongly) names them must still not reach them.
        manifest(root, "v0.4.4", [in_data, outside_roots])
        manifest(root, "v0.4.5", [])
        ps.prune(root)
        check(exists(root, in_data), "data\\ is never pruned, whatever a list says")
        check(exists(root, outside_roots), "nothing outside hermes\\.venv, hermes\\hermes-agent, runtime\\")
        check(exists(root, user_file), "a file no release shipped is in no list, so it stays")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_case_only_rename_does_not_delete_the_new_file():
    print("Windows paths ignore case: Foo.py -> foo.py is the same file")
    root = install()
    try:
        put(root, SITE + "pkg/foo.py", "new")
        manifest(root, "v0.4.4", [SITE + "pkg/Foo.py"])
        manifest(root, "v0.4.5", [SITE + "pkg/foo.py"])
        ps.prune(root)
        check(exists(root, SITE + "pkg/foo.py"), "the renamed file this version ships survives")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_hostile_or_broken_list_cannot_escape():
    print("paths that climb out, are absolute, or use backslashes are ignored")
    root = install()
    victim_dir = tempfile.mkdtemp(prefix="prune-outside-")
    try:
        put(root, "data/keep.txt")
        outside = put(victim_dir, "keep.txt")
        bad = ["../" + os.path.basename(victim_dir) + "/keep.txt",
               "hermes/.venv/../../data/keep.txt",
               "runtime/../data/keep.txt",
               outside.replace("\\", "/"),
               "runtime\\..\\data\\keep.txt",
               "runtime//keep.txt"]
        manifest(root, "v0.4.4", bad)
        manifest(root, "v0.4.5", [])
        ps.prune(root)
        check(exists(root, "data/keep.txt"), "a ..-path into data\\ does nothing")
        check(os.path.exists(outside), "an absolute path outside the install does nothing")
        for p in bad:
            check(not ps._safe(p), "rejected: %s" % p[:50])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(victim_dir, ignore_errors=True)


def test_a_locked_file_is_retried_next_time():
    print("a file that cannot be deleted keeps the older list for next start")
    root = install()
    try:
        rel = "runtime/node-win-x64/node_modules/old/index.js"
        path = put(root, rel)
        os.chmod(path, stat.S_IREAD)
        manifest(root, "v0.4.4", [rel])
        manifest(root, "v0.4.5", [])
        deleted, failed, _ = ps.prune(root)
        if os.name == "nt":
            check(failed == [rel], "the read-only file is reported, not fatal")
            check(exists(root, ps.MANIFEST_DIR + "/files-v0.4.4.txt"), "the older list is kept")
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        deleted, failed, _ = ps.prune(root)
        check(not exists(root, rel) and not failed, "once writable, the next start finishes the job")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_when_it_cannot_know_it_does_nothing():
    print("no VERSION, no list for this version, or a foreign file: no guessing")
    root = tempfile.mkdtemp(prefix="prune-dev-")
    try:
        put(root, SITE + "x.py")
        manifest(root, "v0.4.4", [SITE + "x.py"])
        check(ps.prune(root)[0] == 0 and exists(root, SITE + "x.py"), "a dev tree without VERSION")
        put(root, "VERSION", "v0.4.3\n")
        check(ps.prune(root)[0] == 0 and exists(root, SITE + "x.py"),
              "a pre-manifest release extracted over a newer one (no list for itself)")
        put(root, "VERSION", "v0.4.5\n")
        manifest(root, "v0.4.5", ["not a manifest"], header=False)
        check(ps.prune(root)[0] == 0 and exists(root, SITE + "x.py"),
              "a current list without our header is not trusted")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    check(quiet(ps.main, ["x", os.path.join(tempfile.gettempdir(), "no-such-install")]) == 0,
          "a missing install directory still exits 0 (never blocks the launch)")


def test_a_downgrade_removes_what_the_newer_version_added():
    print("extracting an older release over a newer one")
    root = install("v0.4.5")
    try:
        added_later = SITE + "added_in_046.py"
        put(root, added_later)
        manifest(root, "v0.4.6", [added_later])
        manifest(root, "v0.4.5", [])
        ps.prune(root)
        check(not exists(root, added_later), "a module only the newer engine had is not left to be imported")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_manifest_lists_exactly_what_is_on_disk():
    print("--write-manifest, as CI runs it at packaging time")
    root = install("v0.4.5")
    try:
        for rel in (SITE + "a.py", "hermes/hermes-agent/cli.py", "runtime/uv/uv.exe",
                    "data/config.yaml.default", "scripts/diagnose.py", "Windows-Start.bat"):
            put(root, rel)
        path, count = ps.write_manifest(root, "v0.4.5")
        listed = ps.read_manifest(path)
        check(listed == {SITE + "a.py", "hermes/hermes-agent/cli.py", "runtime/uv/uv.exe"},
              "only the three roots, and all of them (%r)" % sorted(listed or []))
        check(count == 3, "the count it prints is the count it wrote")
        with io.open(path, "rb") as f:
            raw = f.read()
        check(b"\r\n" not in raw and b"\\" not in raw, "LF and forward slashes, whatever the OS")
        check(os.path.basename(path) == "files-v0.4.5.txt", "named for the version, so an older one survives extraction")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_older_lists_ride_along_but_never_replace_this_one():
    print("--write-manifest with the history folder")
    root = install("v0.4.5")
    hist = tempfile.mkdtemp(prefix="prune-hist-")
    try:
        put(root, "runtime/uv/uv.exe")
        for v, body in (("v0.4.3", [SITE + "old.py"]), ("v0.4.5", [SITE + "IMPOSTOR.py"])):
            with open(os.path.join(hist, ps.manifest_name(v) + ".gz"), "wb") as f:
                f.write(gzip.compress(("%s %s\n" % (ps.HEADER, v) + "\n".join(body) + "\n").encode("utf-8")))
        ps.write_manifest(root, "v0.4.5", hist)
        mdir = os.path.join(root, *ps.MANIFEST_DIR.split("/"))
        check(ps.read_manifest(os.path.join(mdir, "files-v0.4.3.txt")) == {SITE + "old.py"},
              "a stored list for an older release is unpacked beside this one")
        check(ps.read_manifest(os.path.join(mdir, "files-v0.4.5.txt")) == {"runtime/uv/uv.exe"},
              "a stored list with this release's name does not overwrite the real one")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(hist, ignore_errors=True)


def test_the_zip_check_catches_a_list_that_disagrees():
    print("--check-zip: the list must be exactly the zip's files under the roots")
    import zipfile
    tmp = tempfile.mkdtemp(prefix="prune-zip-")
    try:
        def make(entries, listed, version="v0.4.5"):
            path = os.path.join(tmp, "p%d.zip" % len(os.listdir(tmp)))
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("VERSION", version + "\n")
                for e in entries:
                    z.writestr(e, "x")
                z.writestr("%s/%s" % (ps.MANIFEST_DIR, ps.manifest_name(version)),
                           "%s %s\n" % (ps.HEADER, version) + "\n".join(listed) + "\n")
            return path
        good = [SITE + "a.py", "runtime/uv/uv.exe"]
        check(ps.check_zip(make(good + ["Windows-Start.bat"], good), "v0.4.5") == [],
              "a matching list passes (files outside the roots are not expected in it)")
        check(any("not in its list" in p for p in ps.check_zip(make(good, good[:1]), "v0.4.5")),
              "a file the list forgot is caught -- an older list naming it would delete it")
        check(any("not in the zip" in p for p in ps.check_zip(make(good[:1], good), "v0.4.5")),
              "a listed file the zip lacks is caught")
        check(any("VERSION" in p for p in ps.check_zip(make(good, good), "v0.4.6")),
              "a VERSION that disagrees with the tag is caught")
        check(quiet(ps.main, ["x", "--check-zip", make(good, good[:1]), "v0.4.5"]) == 1,
              "and the step fails (exit 1)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_lists_for_releases_before_this_existed():
    print("tools/release-manifests: v0.4.3 and v0.4.4 shipped without a list")
    folder = os.path.join(REPO, "tools", "release-manifests")
    for version, marker in (("v0.4.3", "hermes_agent-0.21.3.dist-info/METADATA"),
                            ("v0.4.4", "hermes_agent-0.21.4.dist-info/METADATA")):
        path = os.path.join(folder, ps.manifest_name(version) + ".gz")
        if not os.path.exists(path):
            check(False, "%s is committed" % os.path.basename(path))
            continue
        lines = gzip.decompress(open(path, "rb").read()).decode("utf-8").splitlines()
        check(lines[0] == "%s %s" % (ps.HEADER, version), "%s has our header" % version)
        body = lines[1:]
        check(len(body) > 50000, "%s lists the whole package (%d files)" % (version, len(body)))
        check(all(ps._safe(p) for p in body), "%s: every entry is inside the roots" % version)
        check(SITE + marker in body, "%s: lists its own engine's dist-info" % version)


if __name__ == "__main__":
    for fn in (test_what_v0_4_4_left_behind_is_removed,
               test_nothing_the_user_owns_is_touched,
               test_a_case_only_rename_does_not_delete_the_new_file,
               test_a_hostile_or_broken_list_cannot_escape,
               test_a_locked_file_is_retried_next_time,
               test_when_it_cannot_know_it_does_nothing,
               test_a_downgrade_removes_what_the_newer_version_added,
               test_the_manifest_lists_exactly_what_is_on_disk,
               test_older_lists_ride_along_but_never_replace_this_one,
               test_the_zip_check_catches_a_list_that_disagrees,
               test_the_lists_for_releases_before_this_existed):
        fn()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("prune stale files: all checks passed")
