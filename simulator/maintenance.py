"""Offline maintenance: obtain the same lock as the application first."""

import argparse
from contextlib import closing
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Configuration
from .storage import InstanceLock


def maintain(directory, operation, backup=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(directory / "instance.lock")
    try:
        if operation == "restore":
            backup = Path(backup).resolve()
            if not backup.is_file():
                raise ValueError("备份不存在")
            if backup == (directory / "config.db").resolve():
                raise ValueError("备份不能是正在恢复的配置库")
            with closing(
                sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
            ) as source:
                if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份校验失败")
                if source.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("不支持的备份数据库版本，需停机迁移")
                row = source.execute("SELECT data FROM config WHERE id=1").fetchone()
                if row is not None:
                    Configuration.model_validate_json(row[0])
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                archive = directory / "recovery" / stamp
                archive.mkdir(parents=True)
                for name in ("config.db", "config.db-wal", "config.db-shm"):
                    path = directory / name
                    if path.exists():
                        shutil.copy2(path, archive / name)
                # Build a verified fresh DB before replacing the original.
                candidate = directory / "config.restore.db"
                candidate.unlink(missing_ok=True)
                try:
                    with closing(sqlite3.connect(candidate)) as destination:
                        source.backup(destination)
                    for suffix in ("-wal", "-shm"):
                        (directory / ("config.db" + suffix)).unlink(missing_ok=True)
                    candidate.replace(directory / "config.db")
                finally:
                    candidate.unlink(missing_ok=True)
        elif operation == "compact":
            for path in directory.glob("*.db"):
                with closing(sqlite3.connect(path, timeout=2)) as conn:
                    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError(f"{path.name} 损坏，未执行空间回收")
                    required = path.stat().st_size * 2 + 512 * 1024 * 1024
                    if shutil.disk_usage(directory).free < required:
                        raise ValueError("VACUUM 临时空间不足")
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.execute("VACUUM")
        else:
            raise ValueError("未知维护操作")
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description="停机后备份恢复／空间回收")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("operation", choices=["restore", "compact"])
    parser.add_argument("--backup")
    args = parser.parse_args()
    if args.operation == "restore" and not args.backup:
        parser.error("恢复必须指定 --backup")
    maintain(args.data_dir, args.operation, args.backup)
    print("维护完成，原故障配置已保存（恢复操作），可启动应用")


if __name__ == "__main__":
    main()
