# -*- coding: utf-8 -*-
"""U-Hermes 聊天记录清理 / 数据库瘦身

聊天记录数据库只增不减，而产品里原本没有任何清理入口 —— U 盘迟早被自己
的历史记录塞满，用户还看不出是什么占的地方。

这个脚本做三件事，顺序有讲究：

  1. 删除超过 N 天的旧对话（引擎自己的 prune_sessions）
  2. 让两张 FTS5 索引表 optimize 一次
  3. VACUUM，把空出来的页真正还给文件系统

第 2 步是自己加的。FTS5 删除行时不会真的从索引里拿掉内容，而是写入
"删除标记"，索引因此不降反增；optimize 才会把这些段合并掉。用引擎自带的
schema 造库实测（241 MB / 16000 条消息，删掉 86%）：

    只 prune            —— 文件不变（`hermes sessions prune` 根本不 VACUUM）
    prune + VACUUM      —— 241 MB -> 61 MB   回收 75%
    prune + optimize + VACUUM —— 241 MB -> 34 MB   回收 86%

optimize 只花了不到一秒，白拿十来个百分点。

optimize 的语义和 SQLite 版本有关：3.42 之前一次调用只做部分合并，要循环
多次。随包的运行时是 3.45，一次调用即完整合并。

用法:  db-maintenance.py <data-dir> [--days N] [--yes]
退出码 0 成功 / 1 失败 / 2 服务还在运行，什么都没做
"""
import os
import shutil
import socket
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK, BAD, WARN = "[OK]", "[X] ", "[!] "
MB = 1024 * 1024
DEFAULT_DAYS = 90


def port_busy(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            return True
    except OSError:
        return False


def db_bytes(path):
    """The database plus its sidecars -- all of it is on the user's stick."""
    total = os.path.getsize(path)
    for side in ("-wal", "-shm"):
        if os.path.exists(path + side):
            total += os.path.getsize(path + side)
    return total


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: db-maintenance.py <data-dir> [--days N] [--yes]")
        return 1
    data_dir = args[0]
    days = DEFAULT_DAYS
    assume_yes = "--yes" in args
    if "--days" in args:
        try:
            days = int(args[args.index("--days") + 1])
        except (IndexError, ValueError):
            print("  %s --days 后面要跟一个数字。" % BAD)
            return 1
        if days < 1:
            print("  %s --days 至少是 1。" % BAD)
            return 1

    db_path = os.path.join(data_dir, "state.db")
    if not os.path.exists(db_path):
        print("  %s 还没有聊天记录数据库，无需清理。" % OK)
        return 0

    # VACUUM needs the database to itself. SessionDB opens with BEGIN
    # IMMEDIATE and a 1s timeout, so a running gateway or Web UI turns this
    # into "database is locked" halfway through.
    for port, name in ((8642, "AI 引擎"), (8648, "网页界面")):
        if port_busy(port):
            print()
            print("  %s %s 还在运行（端口 %d）。" % (BAD, name, port))
            print("      请先关掉所有 U-Hermes 窗口，再运行本功能。")
            return 2

    size = db_bytes(db_path)
    print()
    print("  当前聊天记录数据库: %.0f MB" % (size / MB))

    # VACUUM writes a scratch copy into %TEMP% -- on C:, not the stick -- and
    # in WAL mode the copy-back inflates state.db-wal to the size of the
    # whole database. The user reaches for this button exactly when the stick
    # is full, so refuse up front instead of failing halfway.
    try:
        free_here = shutil.disk_usage(data_dir).free
        free_tmp = shutil.disk_usage(tempfile.gettempdir()).free
        short = []
        if free_here < size:
            short.append("U 盘还需 %.0f MB" % ((size - free_here) / MB))
        if free_tmp < size:
            short.append("系统盘还需 %.0f MB" % ((size - free_tmp) / MB))
        if short:
            print()
            print("  %s 空间不够，无法安全整理（%s）。" % (BAD, "，".join(short)))
            print("      整理过程要临时占用和数据库差不多大的空间。")
            print("      先腾出空间，或把 data\\state.db 复制到别处备份后再试。")
            return 1
    except OSError:
        print("  %s 读不到剩余空间，继续尝试。" % WARN)

    os.environ.setdefault("HERMES_HOME", data_dir)
    try:
        from pathlib import Path
        from hermes_state import SessionDB
    except Exception as exc:
        print("  %s 读不到 AI 引擎模块（%s）。" % (BAD, exc.__class__.__name__))
        print("      请通过 Windows-Menu.bat 运行本功能。")
        return 1

    db = SessionDB(Path(db_path))
    conn = db._conn
    cutoff = time.time() - days * 86400
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    # prune only touches ended sessions, so count the same way it does.
    doomed = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE started_at < ? AND ended_at IS NOT NULL",
        (cutoff,),
    ).fetchone()[0]
    print("  共 %d 段对话 / %d 条消息，其中 %d 段是 %d 天前结束的。"
          % (total, messages, doomed, days))

    if not doomed:
        print()
        print("  %s 没有超过 %d 天的旧对话，没有可删除的内容。" % (OK, days))
        print("      要腾出空间，可以换一个更小的天数，例如 30。")
        db.close()
        return 0

    if not assume_yes:
        print()
        print("  将永久删除这 %d 段对话及其全部消息，删掉之后找不回来。" % doomed)
        print("  配置、密钥、记忆和技能都不受影响。")
        try:
            answer = input("  确定吗？输入 y 继续: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print("  已取消。")
            db.close()
            return 0
        if answer not in ("y", "yes"):
            print("  已取消。")
            db.close()
            return 0

    print()
    print("  [1/3] 正在删除旧对话...")
    pruned = db.prune_sessions(older_than_days=days,
                               sessions_dir=Path(data_dir) / "sessions")
    print("        删除了 %d 段。" % pruned)

    # The step the engine never takes. Without it the VACUUM below reclaims
    # noticeably less, because the delete markers are still in the index.
    print("  [2/3] 正在整理搜索索引...")
    for table in ("messages_fts", "messages_fts_trigram"):
        try:
            conn.execute("INSERT INTO %s(%s) VALUES('optimize')" % (table, table))
        except Exception as exc:
            print("        %s %s 整理失败，跳过（%s）" % (WARN, table, exc.__class__.__name__))

    print("  [3/3] 正在压缩数据库（U 盘上可能要几分钟，请不要关窗口）...")
    try:
        db.vacuum()
    except Exception as exc:
        print()
        print("  %s 压缩失败：%s" % (BAD, exc))
        print("      数据没有损坏，旧对话已经删掉了，只是文件暂时没变小。")
        print("      确认没有其它 U-Hermes 窗口在运行后可以再试一次。")
        db.close()
        return 1
    db.close()

    after = db_bytes(db_path)
    print()
    print("  %s 完成：%.0f MB -> %.0f MB，腾出 %.0f MB。"
          % (OK, size / MB, after / MB, max(0, size - after) / MB))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        print("  已中断。数据没有损坏。")
        sys.exit(1)
