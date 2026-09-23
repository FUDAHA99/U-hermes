"""Cleaning up after "extract the new zip over the old one" must only ever
remove files an older release shipped and this one does not.

The cost of getting this wrong is not symmetric. A stale file left behind is
what we had before this existed; a current file deleted, a package the
engine installed at runtime half-deleted, or anything under data\\ touched,
is a broken install or lost chats. Most of the cases below are about what
must survive. Several were found by an adversarial review of the first
version, which reproduced each with real files.

Run:  python portable/scripts/tests/test_prune_stale_files.py
"""
import gzip
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

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


SITE = ps.SITE


def put(root, rel, text="x"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def exists(root, rel):
    return os.path.exists(os.path.join(root, *rel.split("/")))


def manifest(root, version, paths, legacy=False, text=None):
    name = (ps.LEGACY_PREFIX if legacy else "") + ps.manifest_name(version)
    put(root, "%s/%s" % (ps.MANIFEST_DIR, name), ps.render_manifest(version, paths) if text is None else text)


def record(root, dist, paths):
    """A dist-info as pip leaves it: RECORD lists its files, itself included."""
    rel = SITE + dist + "/RECORD"
    rows = [p + ",sha256=x,1" for p in paths] + [dist + "/RECORD,,"]
    put(root, rel, "\n".join(rows) + "\n")
    return rel


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
    print("the real case: a v0.4.4 install (no list of its own) upgraded by extraction")
    root = install("v0.4.5")
    try:
        old_dist = "hermes_agent-0.21.3.dist-info"
        old_meta = SITE + old_dist + "/METADATA"
        old_tool = SITE + "tools/setup_mcp_tool.py"
        old_plugin = SITE + "plugins/model-providers/opencode-free/__init__.py"
        new_meta = SITE + "hermes_agent-0.21.4.dist-info/METADATA"
        shared = SITE + "tools/registry.py"
        for rel in (old_meta, old_tool, old_plugin, new_meta, shared):
            put(root, rel)
        old_record = record(root, old_dist, ["hermes_agent-0.21.3.dist-info/METADATA", "tools/setup_mcp_tool.py",
                                             "plugins/model-providers/opencode-free/__init__.py"])
        manifest(root, "v0.4.3", [old_meta, old_record, old_tool, old_plugin, shared], legacy=True)
        manifest(root, "v0.4.5", [new_meta, shared])

        deleted, failed, _ = ps.prune(root)
        check(deleted == 4 and not failed, "four stale files deleted (%d, %r)" % (deleted, failed))
        check(not exists(root, SITE + old_dist), "the second dist-info is gone, so metadata reports 0.21.4 again")
        check(not exists(root, old_tool), "the tool upstream deleted is not auto-imported any more")
        check(not exists(root, SITE + "plugins/model-providers/opencode-free"),
              "the deleted provider plugin's folder is gone")
        check(exists(root, new_meta) and exists(root, shared), "everything this version ships is still there")
        check(exists(root, SITE + "tools"), "tools/ itself stays: it still holds a current file")
        check(not exists(root, ps.MANIFEST_DIR + "/legacy-files-v0.4.3.txt"), "the stored list is dropped once applied")
        check(exists(root, ps.MANIFEST_DIR + "/files-v0.4.5.txt"), "this version's list stays")
        check(ps.prune(root)[:2] == (0, []), "the next start is a no-op")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stored_lists_are_not_reapplied_after_every_upgrade():
    print("review F1: the stored v0.4.3/v0.4.4 lists come back with every zip")
    root = install("v0.4.7")
    try:
        # Upgrading from v0.4.6, which had a real list. The stored v0.4.4 list
        # comes back with the zip and names telegram, which v0.4.7 dropped
        # and which the engine had since reinstalled at runtime.
        tg = SITE + "telegram/__init__.py"
        put(root, tg)
        put(root, SITE + "runtime_only.py")
        manifest(root, "v0.4.4", [tg, SITE + "runtime_only.py"], legacy=True)
        manifest(root, "v0.4.6", [])
        manifest(root, "v0.4.7", [])
        ps.prune(root)
        check(exists(root, tg) and exists(root, SITE + "runtime_only.py"),
              "with a real older list present, the stored ones are ignored")
        check(not exists(root, ps.MANIFEST_DIR + "/legacy-files-v0.4.4.txt"), "...and cleared away")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_package_reinstalled_at_runtime_is_not_half_deleted():
    print("review F1: the engine installs optional packages into this venv at runtime")
    root = install("v0.4.7")
    try:
        # v0.4.6 shipped python-telegram-bot 22.8. The engine has since
        # upgraded it to 22.9 in place; v0.4.7 no longer ships it at all.
        mods = ["telegram/__init__.py", "telegram/_bot.py"]
        for m in mods:
            put(root, SITE + m)
        record(root, "python_telegram_bot-22.9.dist-info", mods)
        put(root, SITE + "python_telegram_bot-22.9.dist-info/METADATA")
        old_dist = [SITE + "python_telegram_bot-22.8.dist-info/METADATA",
                    SITE + "python_telegram_bot-22.8.dist-info/RECORD"]
        manifest(root, "v0.4.6", [SITE + m for m in mods] + old_dist)
        manifest(root, "v0.4.7", [])
        ps.prune(root)
        check(all(exists(root, SITE + m) for m in mods),
              "files claimed by an installed distribution's RECORD stay")
        check(exists(root, SITE + "python_telegram_bot-22.9.dist-info/METADATA"), "its dist-info stays")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_third_party_packages_are_left_alone():
    print("review 2, F1: a runtime install depends on a package this release stopped shipping")
    root = install("v0.4.7")
    try:
        # v0.4.6 shipped aiohttp (via the messaging extra); v0.4.7 does not.
        # The engine lazily installed edge-tts, which imports aiohttp.
        mods = ["aiohttp/__init__.py", "aiohttp/client.py"]
        for m in mods:
            put(root, SITE + m)
        rec = record(root, "aiohttp-3.14.3.dist-info", mods)
        meta = SITE + "aiohttp-3.14.3.dist-info/METADATA"
        put(root, meta)
        put(root, SITE + "edge_tts/__init__.py", "import aiohttp")
        record(root, "edge_tts-7.2.7.dist-info", ["edge_tts/__init__.py"])
        manifest(root, "v0.4.6", [SITE + m for m in mods] + [rec, meta])
        manifest(root, "v0.4.7", [])
        deleted, failed, _ = ps.prune(root)
        check(deleted == 0 and all(exists(root, SITE + m) for m in mods) and exists(root, meta),
              "aiohttp stays: only the old engine's own files are pruned in the venv (%d deleted)" % deleted)
        check(not failed and not exists(root, ps.MANIFEST_DIR + "/files-v0.4.6.txt"),
              "and that is not a failure: the older list is done with")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_old_engine_bytecode_goes_with_its_source():
    print("the old engine's __pycache__ entries are not in its RECORD")
    root = install("v0.4.5")
    try:
        src = SITE + "tools/connections_tool.py"
        pyc = SITE + "tools/__pycache__/connections_tool.cpython-313.pyc"
        other_pyc = SITE + "tools/__pycache__/registry.cpython-313.pyc"
        for rel in (src, pyc, other_pyc):
            put(root, rel)
        rec = record(root, "hermes_agent-0.21.3.dist-info", ["tools/connections_tool.py"])
        manifest(root, "v0.4.4", [src, pyc, other_pyc, rec])
        manifest(root, "v0.4.5", [])
        ps.prune(root)
        check(not exists(root, src) and not exists(root, pyc), "the stale module and its .pyc are both gone")
        check(exists(root, other_pyc), "a .pyc whose source the old engine does not own stays")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unreadable_engine_record_is_retried():
    print("if the old engine's RECORD cannot be read, nothing of it is guessed at")
    root = install("v0.4.5")
    try:
        mod = SITE + "tools/old.py"
        put(root, mod)
        rec = SITE + "hermes_agent-0.21.3.dist-info/RECORD"
        put(root, rec)
        with open(os.path.join(root, *rec.split("/")), "wb") as f:
            f.write(bytes([0xFF, 0xFE, 0x00]) + b" not utf-8")
        manifest(root, "v0.4.4", [mod, rec])
        manifest(root, "v0.4.5", [])
        deleted, failed, _ = ps.prune(root)
        check(exists(root, mod) and bool(failed), "its files stay, and it counts as not done (%r)" % failed)
        check(exists(root, ps.MANIFEST_DIR + "/files-v0.4.4.txt"), "the older list is kept for next start")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stored_lists_get_one_turn_per_install():
    print("review 2, F2: re-extracting the same release brings the stored lists back")
    root = install("v0.5.0")
    try:
        # After the first start only files-v0.5.0.txt and the marker are left.
        manifest(root, "v0.5.0", [])
        ps.prune(root)
        check(exists(root, ps.MANIFEST_DIR + "/" + ps.LEGACY_DONE), "the first start leaves the marker")
        # The engine then lazily installed python-telegram-bot 22.8, the very
        # version v0.4.4 shipped, so its dist-info name is in the stored list.
        tg = SITE + "telegram/__init__.py"
        put(root, tg)
        rec = record(root, "python_telegram_bot-22.8.dist-info", ["telegram/__init__.py"])
        # Outside the venv the engine-only rule does not apply, so only the
        # marker stands between a re-applied list and, say, a package the
        # user later installed with npm -g at a path v0.4.4 once shipped.
        npm_global = "runtime/node-win-x64/node_modules/some-cli/index.js"
        put(root, npm_global)
        # The diagnose tool's advice, 请重新解压安装包: the same zip again.
        manifest(root, "v0.4.4", [tg, rec, npm_global], legacy=True)
        deleted, _f, _ = ps.prune(root)
        check(deleted == 0 and exists(root, tg) and exists(root, rec) and exists(root, npm_global),
              "nothing is deleted (%d)" % deleted)
        check(not exists(root, ps.MANIFEST_DIR + "/legacy-files-v0.4.4.txt"), "the stored list is just cleared away")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stored_and_real_lists_both_apply_on_the_first_start():
    print("v0.4.5 extracted over v0.4.4 but never started, then v0.4.6 over that")
    root = install("v0.4.6")
    try:
        from_043 = SITE + "tools/setup_mcp_tool.py"
        from_045 = "hermes/hermes-agent/gone_in_046.py"
        for rel in (from_043, from_045):
            put(root, rel)
        rec = record(root, "hermes_agent-0.21.3.dist-info", ["tools/setup_mcp_tool.py"])
        manifest(root, "v0.4.3", [from_043, rec], legacy=True)
        manifest(root, "v0.4.5", [from_045])
        manifest(root, "v0.4.6", [])
        ps.prune(root)
        check(not exists(root, from_043) and not exists(root, from_045),
              "no marker yet: the stored list applies alongside the real one")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_network_paths_use_the_unc_long_form():
    print("review 2, F3: a share path needs the UNC long form")
    if os.name != "nt":
        print("  skip  Windows only")
        return
    b = chr(92)
    unc = b * 2 + "server" + b + "share" + b + "U-Hermes"
    check(ps._ext(unc) == b * 2 + "?" + b + "UNC" + b + "server" + b + "share" + b + "U-Hermes",
          "the extended form of a share path")
    check(ps._plain(ps._ext(unc)) == unc, "and back")


def test_nothing_the_user_owns_is_touched():
    print("data\\, other folders and files no release shipped survive")
    root = install()
    try:
        in_data = "data/state.db"
        outside_roots = "scripts/old_helper.py"
        user_file = SITE + "someone_pip_installed.py"
        for rel in (in_data, outside_roots, user_file):
            put(root, rel)
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
    print("paths that climb out, are absolute, use backslashes or alias names are ignored")
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
               "runtime//keep.txt",
               "runtime/foo.py.",
               "runtime/foo.py "]
        manifest(root, "v0.4.4", bad)
        manifest(root, "v0.4.5", [])
        ps.prune(root)
        check(exists(root, "data/keep.txt"), "a ..-path into data\\ does nothing")
        check(os.path.exists(outside), "an absolute path outside the install does nothing")
        for p in bad:
            check(not ps._safe(p), "rejected: %r" % p[:50])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(victim_dir, ignore_errors=True)


def test_a_junction_inside_the_install_is_not_followed():
    print("review F4: a junction under runtime\\ must not lead the delete outside")
    if os.name != "nt":
        print("  skip  Windows only")
        return
    root = install()
    outside = tempfile.mkdtemp(prefix="prune-junction-target-")
    try:
        victim = put(outside, "sub/victim.txt")
        put(outside, "keepme.txt")
        os.makedirs(os.path.join(root, "runtime"), exist_ok=True)
        link = os.path.join(root, "runtime", "jx")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, outside], capture_output=True)
        if r.returncode != 0:
            print("  skip  could not create a junction here")
            return
        manifest(root, "v0.4.4", ["runtime/jx/sub/victim.txt"])
        manifest(root, "v0.4.5", [])
        ps.prune(root)
        check(os.path.exists(victim), "the file behind the junction survives")
        check(os.path.isdir(link), "the junction itself is not removed")
    finally:
        link = os.path.join(root, "runtime", "jx")
        if os.path.isdir(link):
            os.rmdir(link)  # removes the junction, not its target
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_a_long_path_is_deleted_not_skipped_and_forgotten():
    print("review F5: a stale file beyond MAX_PATH must not be treated as absent")
    if os.name != "nt":
        print("  skip  Windows only")
        return
    root = install()
    try:
        rel = "runtime/" + "/".join(["d%02d_%s" % (i, "x" * 20) for i in range(14)]) + "/deep.js"
        full = "\\\\?\\" + os.path.join(os.path.abspath(root), *rel.split("/"))
        os.makedirs(os.path.dirname(full))
        open(full, "w").close()
        check(len(os.path.join(root, *rel.split("/"))) > 300, "the path really is long (%d)" % len(os.path.join(root, *rel.split("/"))))
        manifest(root, "v0.4.4", [rel])
        manifest(root, "v0.4.5", [])
        deleted, failed, _ = ps.prune(root)
        check(deleted == 1 and not os.path.exists(full), "it is deleted (%d, %r)" % (deleted, failed))
    finally:
        shutil.rmtree("\\\\?\\" + os.path.abspath(root), ignore_errors=True)


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
        if os.path.exists(path):  # POSIX deletes a read-only file in a writable folder
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        deleted, failed, _ = ps.prune(root)
        check(not exists(root, rel) and not failed, "once writable, the next start finishes the job")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_interrupted_extraction_deletes_nothing_new():
    print("review F3: VERSION still says the old release, the new list is already there")
    root = install("v0.4.5")
    try:
        added = SITE + "added_in_046.py"
        put(root, added)
        manifest(root, "v0.4.6", [added])
        manifest(root, "v0.4.5", [])
        deleted, _f, _ = ps.prune(root)
        check(deleted == 0 and exists(root, added), "a newer list is never applied")
        check(exists(root, ps.MANIFEST_DIR + "/files-v0.4.6.txt"),
              "and it stays, for when the extraction is finished")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_torn_list_is_not_a_short_list():
    print("review F2: a list cut short must read as unreadable, not as complete")
    root = install("v0.4.5")
    try:
        kept = [SITE + "a.py", SITE + "b.py", "runtime/node-win-x64/node.exe"]
        for rel in kept:
            put(root, rel)
        manifest(root, "v0.4.3", kept, legacy=True)
        whole = ps.render_manifest("v0.4.5", kept)
        manifest(root, "v0.4.5", None, text=whole[: len(whole) // 2])
        check(ps.prune(root)[0] == 0 and all(exists(root, r) for r in kept),
              "a torn current list: nothing is deleted")
        manifest(root, "v0.4.5", None, text=whole.replace("# end 3", "# end 5"))
        check(ps.prune(root)[0] == 0 and all(exists(root, r) for r in kept),
              "a count that does not match: nothing is deleted")
        check(ps.parse_manifest(whole) == set(kept), "the whole list parses")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_when_it_cannot_know_it_does_nothing():
    print("no VERSION, no list for this version, a foreign file, a CI build")
    root = tempfile.mkdtemp(prefix="prune-dev-")
    try:
        put(root, SITE + "x.py")
        manifest(root, "v0.4.4", [SITE + "x.py"])
        check(ps.prune(root)[0] == 0 and exists(root, SITE + "x.py"), "a dev tree without VERSION")
        put(root, "VERSION", "v0.4.3\n")
        check(ps.prune(root)[0] == 0 and exists(root, SITE + "x.py"),
              "a pre-manifest release extracted over a newer one (no list for itself)")
        put(root, "VERSION", "v0.4.5\n")
        manifest(root, "v0.4.5", None, text="not a manifest\n")
        check(ps.prune(root)[0] == 0 and exists(root, SITE + "x.py"),
              "a current list without our header is not trusted")
        put(root, "VERSION", "0.0.0-ci\n")
        manifest(root, "0.0.0-ci", [])
        check(ps.prune(root)[0] == 0 and exists(root, SITE + "x.py"), "a CI build has no release order")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    check(quiet(ps.main, ["x", os.path.join(tempfile.gettempdir(), "no-such-install")]) == 0,
          "a missing install directory still exits 0 (never blocks the launch)")


def test_the_list_is_built_from_the_zip_itself():
    print("--add-to-zip, as CI runs it right after Compress-Archive")
    tmp = tempfile.mkdtemp(prefix="prune-zip-")
    try:
        hist = os.path.join(tmp, "hist")
        os.makedirs(hist)
        for v, body in (("v0.4.3", [SITE + "old.py"]), ("v0.4.5", [SITE + "IMPOSTOR.py"])):
            with open(os.path.join(hist, ps.manifest_name(v) + ".gz"), "wb") as f:
                f.write(gzip.compress(ps.render_manifest(v, body).encode("utf-8")))
        zpath = os.path.join(tmp, "p.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("VERSION", "v0.4.5\n")
            for e in (SITE + "a.py", "hermes/hermes-agent/cli.py", "runtime/uv/uv.exe",
                      "data/config.yaml.default", "Windows-Start.bat", "runtime/empty-dir/"):
                z.writestr(e, "" if e.endswith("/") else "x")
        count, names = ps.add_to_zip(zpath, "v0.4.5", hist)
        with zipfile.ZipFile(zpath) as z:
            listed = ps.parse_manifest(z.read(ps.MANIFEST_DIR + "/files-v0.4.5.txt").decode("utf-8"))
            legacy = ps.parse_manifest(z.read(ps.MANIFEST_DIR + "/legacy-files-v0.4.3.txt").decode("utf-8"))
            all_names = z.namelist()
        check(listed == {SITE + "a.py", "hermes/hermes-agent/cli.py", "runtime/uv/uv.exe"},
              "exactly the zip's files under the roots (%r)" % sorted(listed or []))
        check(count == 3, "the count it reports is the count it wrote")
        check(legacy == {SITE + "old.py"}, "a stored older list rides along as legacy-files-*")
        check(ps.MANIFEST_DIR + "/legacy-files-v0.4.5.txt" not in all_names,
              "a stored list with this release's name is not shipped")
        check(ps.check_zip(zpath, "v0.4.5") == [], "and the verify step's check agrees")
        try:
            ps.add_to_zip(zpath, "v0.4.5", hist)
            check(False, "adding twice is refused")
        except ValueError:
            check(True, "adding twice is refused")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_zip_check_catches_a_list_that_disagrees():
    print("--check-zip: the list must be exactly the zip's files under the roots")
    tmp = tempfile.mkdtemp(prefix="prune-zip-")
    try:
        def make(entries, listed, version="v0.4.5"):
            path = os.path.join(tmp, "p%d.zip" % len(os.listdir(tmp)))
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("VERSION", version + "\n")
                for e in entries:
                    z.writestr(e, "x")
                z.writestr("%s/%s" % (ps.MANIFEST_DIR, ps.manifest_name(version)),
                           ps.render_manifest(version, listed))
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
        text = gzip.decompress(open(path, "rb").read()).decode("utf-8")
        body = ps.parse_manifest(text)
        check(body is not None and text.startswith("%s %s\n" % (ps.HEADER, version)),
              "%s is a whole list with our header and trailer" % version)
        body = body or set()
        check(len(body) > 50000, "%s lists the whole package (%d files)" % (version, len(body)))
        check(all(ps._safe(p) for p in body), "%s: every entry is inside the roots" % version)
        check(SITE + marker in body, "%s: lists its own engine's dist-info" % version)
        check(not any("/.git/" in p for p in body), "%s: nothing Compress-Archive leaves out" % version)


if __name__ == "__main__":
    for fn in (test_what_v0_4_4_left_behind_is_removed,
               test_stored_lists_are_not_reapplied_after_every_upgrade,
               test_a_package_reinstalled_at_runtime_is_not_half_deleted,
               test_third_party_packages_are_left_alone,
               test_old_engine_bytecode_goes_with_its_source,
               test_an_unreadable_engine_record_is_retried,
               test_stored_lists_get_one_turn_per_install,
               test_stored_and_real_lists_both_apply_on_the_first_start,
               test_network_paths_use_the_unc_long_form,
               test_nothing_the_user_owns_is_touched,
               test_a_case_only_rename_does_not_delete_the_new_file,
               test_a_hostile_or_broken_list_cannot_escape,
               test_a_junction_inside_the_install_is_not_followed,
               test_a_long_path_is_deleted_not_skipped_and_forgotten,
               test_a_locked_file_is_retried_next_time,
               test_an_interrupted_extraction_deletes_nothing_new,
               test_a_torn_list_is_not_a_short_list,
               test_when_it_cannot_know_it_does_nothing,
               test_the_list_is_built_from_the_zip_itself,
               test_the_zip_check_catches_a_list_that_disagrees,
               test_the_lists_for_releases_before_this_existed):
        fn()
    print("")
    if FAILURES:
        print("%d check(s) failed" % len(FAILURES))
        sys.exit(1)
    print("prune stale files: all checks passed")
