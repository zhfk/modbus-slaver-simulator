import asyncio
import concurrent.futures
import contextlib
import hashlib
import json
import os
import queue
import shutil
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

from .errors import DomainError
from .models import Configuration

MiB = 1024 * 1024


def atomic_json(path, content, previous=None):
    payload = json.dumps(
        content, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    envelope = {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "payload": payload.decode(),
    }
    encoded = json.dumps(envelope, ensure_ascii=False).encode()
    if len(encoded) > 32 * MiB:
        raise ValueError("快照超过 32MiB，保留最近成功版本")
    temp = path.with_suffix(".tmp")
    previous_temp = previous.with_suffix(".tmp") if previous else None
    try:
        with open(temp, "wb") as file:
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        if previous and path.exists():
            try:
                read_json(path)
            except (ValueError, KeyError, TypeError, FileNotFoundError):
                # A corrupt latest snapshot must not replace a valid fallback.
                pass
            else:
                with open(previous_temp, "wb") as file:
                    file.write(path.read_bytes())
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(previous_temp, previous)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
        if previous_temp:
            previous_temp.unlink(missing_ok=True)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read_json(path):
    if path.stat().st_size > 32 * MiB:
        raise ValueError("快照过大")
    content = json.loads(path.read_text(encoding="utf-8"))
    payload = content["payload"]
    if hashlib.sha256(payload.encode()).hexdigest() != content["sha256"]:
        raise ValueError("快照校验失败")
    return json.loads(payload)


class InstanceLock:
    def __init__(self, path):
        self.file = open(path, "a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("同一数据目录已有应用实例") from exc

    def close(self):
        self.file.close()


class DatabaseWorker:
    """Fixed owner thread with a bounded request queue and real outcomes."""

    def __init__(self, path, schema, full=True, budget=64 * MiB, readonly=False):
        self.path, self.queue = path, queue.Queue(maxsize=32)
        self.closing = threading.Event()
        self.progress, self.error = time.monotonic(), ""
        self.busy = False
        self.busy_since = self.progress
        self.ready = concurrent.futures.Future()
        self.thread = threading.Thread(
            target=self.run,
            args=(schema, full, budget, readonly),
            name=path.stem + "-writer",
            daemon=True,
        )
        self.thread.start()
        self.ready.result(timeout=5)

    def run(self, schema, full, budget, readonly):
        conn = None
        try:
            conn = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro" if readonly else self.path,
                timeout=2,
                uri=readonly,
            )
            if readonly:
                conn.execute("PRAGMA query_only=ON")
            else:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA synchronous={'FULL' if full else 'NORMAL'}")
            conn.execute("PRAGMA busy_timeout=2000")
            conn.execute("PRAGMA wal_autocheckpoint=128")
            if not readonly:
                conn.execute(
                    f"PRAGMA max_page_count={max(64, int(budget * 0.7) // 4096)}"
                )
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("不支持的数据库版本，需停机迁移")
            if not readonly:
                conn.executescript(schema)
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError("数据库校验失败：" + result)
            self.ready.set_result(True)
        except Exception as exc:
            if conn is not None:
                conn.close()
            self.error = str(exc)
            self.ready.set_exception(exc)
            return
        while not self.closing.is_set() or not self.queue.empty():
            try:
                job = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            function, future = job
            self.busy, self.busy_since = True, time.monotonic()
            try:
                result = function(conn)
                self.progress, self.error = time.monotonic(), ""
                future.set_result(result)
            except Exception as exc:
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                self.error = str(exc)
                self.progress = time.monotonic()
                future.set_exception(exc)
            finally:
                self.busy = False
        if not readonly:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

    def submit(self, function):
        if self.closing.is_set() or not self.thread.is_alive():
            raise DomainError("数据库写入器已停止", 503)
        future = concurrent.futures.Future()
        try:
            self.queue.put_nowait((function, future))
        except queue.Full as exc:
            raise DomainError("数据库队列已满", 503) from exc
        return future

    async def call(self, function, timeout=5):
        future = self.submit(function)
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout)
        except TimeoutError as exc:
            # Do not cancel the actual transaction. Consumers must reconcile.
            wrapped.add_done_callback(
                lambda f: f.exception() if not f.cancelled() else None
            )
            raise DomainError("数据库结果待核对，请刷新状态", 503) from exc
        except sqlite3.Error as exc:
            raise DomainError("数据库操作失败：" + str(exc), 503) from exc

    async def close(self):
        self.closing.set()
        await asyncio.to_thread(self.thread.join, 3)
        return not self.thread.is_alive()


CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, data TEXT NOT NULL);
PRAGMA user_version=1;
"""
TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, time REAL NOT NULL, device TEXT NOT NULL, point TEXT NOT NULL, version INTEGER NOT NULL, value REAL, raw TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sample_lookup ON samples(device,point,time,id);
CREATE INDEX IF NOT EXISTS sample_time ON samples(time);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY,time REAL NOT NULL,data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_time ON events(time);
CREATE TABLE IF NOT EXISTS assignments (id INTEGER PRIMARY KEY,time REAL NOT NULL,device TEXT NOT NULL,operation TEXT NOT NULL UNIQUE,data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS assignment_lookup ON assignments(device,time DESC,operation DESC);
PRAGMA user_version=1;
"""


class Storage:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = InstanceLock(self.directory / "instance.lock")
        self.backups, self.tmp = self.directory / "backups", self.directory / "tmp"
        self.backups.mkdir(exist_ok=True)
        self.tmp.mkdir(exist_ok=True)
        self.config_db = self.telemetry_db = self.telemetry_reader = None
        self.error = ""
        self.history_error = ""
        self.backup_error = self.snapshot_error = ""
        self.config = Configuration()
        self.queue = deque()
        self.queue_bytes = self.dropped = 0
        self.assignment_pruned = 0
        self.last_snapshot = self.last_backup = 0
        self.restore_report = {}
        self.last_history = self.last_cleanup = 0
        self.flush_task = None
        self.config_pending = False
        self.commit_lock = asyncio.Lock()
        self.snapshot_busy = False
        self.metrics_cache = {}

    async def open(self):
        try:
            self.config_db = await asyncio.to_thread(
                DatabaseWorker, self.directory / "config.db", CONFIG_SCHEMA
            )
            row = await self.config_db.call(
                lambda c: c.execute("SELECT data FROM config WHERE id=1").fetchone()
            )
            if row:
                # Older versions allowed wildcard/specific listeners on one
                # port. Keep them accessible for correction; new API/import
                # submissions still undergo the full endpoint validation.
                self.config = Configuration.model_validate_json(
                    row[0], context={"allow_legacy_endpoint_overlap": True}
                )
        except Exception as exc:
            self.error = str(exc)
        # A damaged optional history database must not prevent a validated
        # configuration from serving Modbus or accepting configuration edits.
        try:
            self.telemetry_db = await asyncio.to_thread(
                DatabaseWorker,
                self.directory / "telemetry.db",
                TELEMETRY_SCHEMA,
                False,
                self.config.settings.telemetry_budget_mb * MiB,
            )
            self.telemetry_reader = await asyncio.to_thread(
                DatabaseWorker,
                self.directory / "telemetry.db",
                TELEMETRY_SCHEMA,
                False,
                self.config.settings.telemetry_budget_mb * MiB,
                True,
            )
        except Exception as exc:
            self.history_error = "历史库不可用：" + str(exc)
        self.refresh_metrics()

    def refresh_metrics(self):
        paths = (
            list(self.directory.glob("*.db*"))
            + list(self.directory.glob("snapshot*.json"))
            + list(self.directory.glob("application.log*"))
            + list(self.backups.glob("*.db"))
            + list(self.tmp.glob("*"))
        )
        sizes = {}
        for path in paths:
            try:
                if path.is_file():
                    sizes[str(path.relative_to(self.directory))] = path.stat().st_size
            except FileNotFoundError:
                continue
        try:
            oldest = self.queue[0]
        except IndexError:
            oldest = None
        self.metrics_cache = {
            "files": sizes,
            "disk_free": shutil.disk_usage(self.directory).free,
            "queue_count": len(self.queue),
            "queue_bytes": self.queue_bytes,
            "oldest_seconds": max(
                0,
                time.time()
                - (oldest[1][0] if oldest[0] == "sample" else oldest[1]["time"]),
            )
            if oldest
            else 0,
            "dropped": self.dropped,
            "last_snapshot": self.last_snapshot,
            "last_backup": self.last_backup,
            "error": self.error,
            "history_error": self.nonessential_errors(),
            "pending": self.config_pending,
            "restore": self.restore_report,
        }

    def writable(self):
        return (
            not self.error
            and self.metrics_cache.get("disk_free", 0)
            >= self.config.settings.disk_reserve_mb * MiB
        )

    def nonessential_errors(self):
        return "；".join(
            filter(None, (self.history_error, self.backup_error, self.snapshot_error))
        )

    async def commit(self, new, runtime, check=None):
        if not self.writable() or not self.config_db:
            raise DomainError("持久化不可用或磁盘余量不足，配置未保存", 503)
        if self.config_pending or self.commit_lock.locked():
            raise DomainError("另一个配置变更正在进行，请稍后刷新", 409)
        async with self.commit_lock:
            if new.version != self.config.version:
                raise DomainError("配置已被其他操作修改，请刷新后比较", 409)
            if check:
                check()
            new = new.model_copy(update={"version": self.config.version + 1})
            text = new.model_dump_json()
            if len(text.encode()) > min(
                16 * MiB, new.settings.config_budget_mb * MiB // 2
            ):
                raise DomainError("配置大小超过预算")
            # Allocate and compile before persistence; inherit current values only
            # at activation, so writes received during the DB await are retained.
            try:
                prepared = runtime.prepare(new)
            except (MemoryError, ValueError, TypeError) as exc:
                raise DomainError("无法准备新点位映射，配置未保存", 503) from exc
            self.config_pending = True

            def write(conn):
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO config VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,data=excluded.data",
                    (new.version, text),
                )
                conn.commit()
                return new.version

            try:
                future = self.config_db.submit(write)
            except DomainError:
                self.config_pending = False
                raise

            async def activate():
                try:
                    await asyncio.wrap_future(future)
                    runtime.apply(new, prepared)
                    self.config = new
                    runtime.event("config", "", f"配置版本 {new.version} 已生效")
                    self.config_pending = False
                except Exception as exc:
                    self.error = "配置提交或激活待核对：" + str(exc)
                    # Reading the committed version resolves uncertainty;
                    # never repeat a timed-out transaction blindly.
                    try:
                        row = await self.config_db.call(
                            lambda c: c.execute(
                                "SELECT data FROM config WHERE id=1"
                            ).fetchone()
                        )
                        recovered = (
                            Configuration.model_validate_json(
                                row[0], context={"allow_legacy_endpoint_overlap": True}
                            )
                            if row
                            else self.config
                        )
                        runtime.apply(recovered)
                        self.config = recovered
                        self.config_pending, self.error = False, ""
                    except Exception:
                        pass
                    raise
                # A verified configuration commit remains successful if the
                # additional backup cannot be written. Report its failure
                # separately; do not suggest the committed edit was rolled back.
                try:
                    await self.backup()
                    self.backup_error = ""
                except Exception as exc:
                    self.backup_error = "备份失败：" + str(exc)

            task = asyncio.create_task(activate())
            task.add_done_callback(
                lambda f: f.exception() if not f.cancelled() else None
            )
            try:
                await asyncio.wait_for(asyncio.shield(task), 5)
            except TimeoutError as exc:
                raise DomainError("保存结果待核对，请刷新配置版本", 503) from exc
            except Exception as exc:
                raise DomainError("配置保存失败：" + str(exc), 503) from exc
            return self.config

    async def backup(self):
        if not self.config_db or not self.writable():
            return
        dest = self.backups / "config.new.db"

        def make(conn):
            # Connection context managers commit/rollback but do not close;
            # Windows cannot rotate a backup while its handle remains open.
            with contextlib.closing(sqlite3.connect(dest)) as backup:
                conn.backup(backup, pages=128)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("备份校验失败")
            if dest.stat().st_size * 8 > self.config.settings.backup_budget_mb * MiB:
                dest.unlink()
                raise RuntimeError("备份超过预算")
            for index in range(5, -1, -1):
                source = self.backups / f"config.{index}.db"
                if source.exists():
                    os.replace(source, self.backups / f"config.{index + 1}.db")
            os.replace(dest, self.backups / "config.0.db")

        await self.config_db.call(make, timeout=10)
        self.last_backup = time.time()

    def enqueue(self, kind, row):
        size = len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
        if size > 65536:
            self.dropped += 1
            return
        while self.queue and (
            len(self.queue) >= 10000 or self.queue_bytes + size > 16 * MiB
        ):
            _, _, old_size = self.queue.popleft()
            self.queue_bytes -= old_size
            self.dropped += 1
        self.queue.append((kind, row, size))
        self.queue_bytes += size

    def sample(self, runtime):
        now, settings = time.monotonic(), self.config.settings
        if (
            not settings.history_enabled
            or now - self.last_history < settings.history_interval
            or not self.writable()
        ):
            return
        self.last_history = now
        selected = set(settings.history_points)
        for device in runtime.devices.values():
            if device.status != "running":
                continue
            for key, point in device.points.items():
                if key not in selected:
                    continue
                value = device.value(key)
                from .codec import safe_number

                self.enqueue(
                    "sample",
                    (
                        time.time(),
                        device.config.id,
                        key,
                        self.config.version,
                        safe_number(value),
                        json.dumps(device.raw(key)),
                        json.dumps(
                            {
                                "name": point.name,
                                "unit": point.unit,
                                "type": point.type,
                                "layout": point.layout(),
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )

    async def flush(self):
        if not self.queue or not self.telemetry_db or not self.writable():
            return
        batch = []
        batch_bytes = 0
        # Leave room for indexes and WAL when the physical budget is small.
        byte_limit = max(
            65536, int(self.config.settings.telemetry_budget_mb * MiB * 0.05)
        )
        for _ in range(min(1000, len(self.queue))):
            if batch and batch_bytes + self.queue[0][2] > byte_limit:
                break
            kind, row, size = self.queue.popleft()
            self.queue_bytes -= size
            batch_bytes += size
            batch.append((kind, row))

        def persist(conn):
            pruned = 0
            budget = self.config.settings.telemetry_budget_mb * MiB
            conn.execute(f"PRAGMA max_page_count={max(64, int(budget * 0.7) // 4096)}")
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
            if (pages - free) * 4096 + batch_bytes * 2 > budget * 0.55:
                for table in ("samples", "events", "assignments"):
                    deleted = conn.execute(
                        f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY time LIMIT 1000)"
                    )
                    if table == "assignments":
                        pruned += deleted.rowcount
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            assignment_devices = set()
            for kind, row in batch:
                if kind == "sample":
                    conn.execute(
                        "INSERT INTO samples(time,device,point,version,value,raw,metadata) VALUES(?,?,?,?,?,?,?)",
                        row,
                    )
                else:
                    data = json.dumps(row, ensure_ascii=False)
                    if row.get("kind") in ("manual", "external_write") and row.get(
                        "id"
                    ):
                        conn.execute(
                            "INSERT INTO assignments(time,device,operation,data) VALUES(?,?,?,?) ON CONFLICT(operation) DO NOTHING",
                            (row["time"], row["device"], row["id"], data),
                        )
                        assignment_devices.add(row["device"])
                    else:
                        conn.execute(
                            "INSERT INTO events(time,data) VALUES(?,?)",
                            (row["time"], data),
                        )
            for device in assignment_devices:
                conn.execute(
                    "DELETE FROM assignments WHERE device=? AND id NOT IN (SELECT id FROM assignments WHERE device=? ORDER BY time DESC,operation DESC LIMIT 100)",
                    (device, device),
                )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            return pruned

        try:
            self.assignment_pruned += await self.telemetry_db.call(persist)
            self.history_error = ""
        except Exception as exc:
            self.dropped += len(batch)
            self.history_error = str(exc)

    async def cleanup(self):
        cutoff = time.time() - self.config.settings.retention_days * 86400
        device_ids = [device.id for device in self.config.devices]
        if self.telemetry_db:

            def clean(conn):
                pruned = 0
                # Assignment retention is count-based for live devices.
                placeholders = ",".join("?" for _ in device_ids) or "NULL"
                removed = f"device NOT IN ({placeholders})" if device_ids else "1=1"
                conn.execute(
                    f"DELETE FROM assignments WHERE id IN (SELECT id FROM assignments WHERE {removed} LIMIT 1000)",
                    device_ids,
                )
                for table in ("samples", "events"):
                    conn.execute(
                        f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE time<? LIMIT 1000)",
                        (cutoff,),
                    )
                # Also free reusable pages before the physical budget is hit.
                pages = conn.execute("PRAGMA page_count").fetchone()[0]
                free = conn.execute("PRAGMA freelist_count").fetchone()[0]
                if (
                    pages - free
                ) * 4096 > self.config.settings.telemetry_budget_mb * MiB * 0.55:
                    for table in ("samples", "events", "assignments"):
                        deleted = conn.execute(
                            f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY time LIMIT 1000)"
                        )
                        if table == "assignments":
                            pruned += deleted.rowcount
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                return pruned

            try:
                self.assignment_pruned += await self.telemetry_db.call(clean)
            except Exception as exc:
                self.history_error = str(exc)
        for path in self.tmp.glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < time.time() - 86400:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass

    async def snapshot(self, runtime):
        if self.snapshot_busy or not self.writable():
            return
        self.snapshot_busy = True
        try:
            content = {
                "format": 1,
                "version": self.config.version,
                "time": time.time(),
                "devices": {
                    key: device.snapshot() for key, device in runtime.devices.items()
                },
            }
            path = self.directory / "snapshot.json"

            def save():
                atomic_json(path, content, self.directory / "snapshot.previous.json")

            await asyncio.to_thread(save)
            self.last_snapshot = time.time()
            self.snapshot_error = ""
        except Exception as exc:
            self.snapshot_error = "快照失败：" + str(exc)
        finally:
            self.snapshot_busy = False

    async def restore(self, runtime):
        for name in ("snapshot.json", "snapshot.previous.json"):
            path = self.directory / name
            if not path.exists():
                continue
            try:
                content = await asyncio.to_thread(read_json, path)
                if (
                    content["format"] != 1
                    or time.time() - content["time"]
                    > self.config.settings.snapshot_max_age
                ):
                    raise ValueError("快照过期或版本不兼容")
                from .runtime import DeviceRuntime

                fresh = {d.id: DeviceRuntime(d) for d in self.config.devices}
                report = {
                    key: device.restore_snapshot(content["devices"].get(key, {}))
                    for key, device in fresh.items()
                }
                # Activate restored maps through the same path as configuration
                # changes so all devices regain their audit callback. Inheriting
                # the initial maps here would overwrite restored values/states.
                runtime.apply(self.config, fresh, inherit=False)
                self.restore_report = report
                return
            except Exception as exc:
                self.restore_report = {"error": str(exc)}

    async def history(self, device, point, start, end, limit=1000, after=0):
        if not self.telemetry_reader:
            raise DomainError("历史库不可用", 503)
        if not start < end or end - start > 366 * 86400 or not 1 <= limit <= 1000:
            raise DomainError("时间范围／结果上限无效")

        def query(conn):
            deadline = time.monotonic() + 2
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                return conn.execute(
                    "SELECT id,time,value,raw,metadata,version FROM samples WHERE device=? AND point=? AND time>=? AND time<=? AND id>? ORDER BY id LIMIT ?",
                    (device, point, start, end, after, limit),
                ).fetchall()
            finally:
                conn.set_progress_handler(None, 0)

        rows = await self.telemetry_reader.call(query, timeout=3)
        return [
            {
                "id": r[0],
                "time": r[1],
                "value": r[2],
                "raw": json.loads(r[3]),
                "metadata": json.loads(r[4]),
                "version": r[5],
            }
            for r in rows
        ]

    async def assignments(self, device):
        if not self.telemetry_reader:
            raise DomainError("赋值历史库不可用，本次运行的缓存仍可查看", 503)

        def query(conn):
            deadline = time.monotonic() + 2
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                return conn.execute(
                    "SELECT data FROM assignments WHERE device=? ORDER BY time DESC,operation DESC LIMIT 100",
                    (device,),
                ).fetchall()
            finally:
                conn.set_progress_handler(None, 0)

        try:
            rows = await self.telemetry_reader.call(query, timeout=3)
            result = [json.loads(row[0]) for row in rows]
            if any(
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not isinstance(row.get("time"), (float, int))
                or not isinstance(row.get("changes"), list)
                or len(row["changes"]) > 20
                or not isinstance(row.get("count"), int)
                or row.get("outcome") not in ("success", "failed")
                or any(
                    not isinstance(change, dict)
                    or not {
                        "id",
                        "name",
                        "type",
                        "area",
                        "address",
                        "before",
                        "after",
                        "before_raw",
                        "after_raw",
                    }.issubset(change)
                    or not isinstance(change["before_raw"], list)
                    or not isinstance(change["after_raw"], list)
                    for change in row["changes"]
                )
                for row in result
            ):
                raise ValueError("无效赋值记录")
            return result
        except Exception as exc:
            raise DomainError("赋值历史读取失败，本次运行的缓存仍可查看", 503) from exc

    async def maintain(self, runtime):
        while True:
            await asyncio.sleep(1)
            await asyncio.to_thread(self.refresh_metrics)
            now = time.monotonic()
            await self.flush()
            if now - self.last_cleanup >= 10:
                await self.cleanup()
                self.last_cleanup = now
            if (
                time.time() - self.last_snapshot
                >= self.config.settings.snapshot_seconds
            ):
                await self.snapshot(runtime)
            if self.config_db and time.time() - self.last_backup >= 86400:
                try:
                    await self.backup()
                    self.backup_error = ""
                except Exception as exc:
                    self.backup_error = "备份失败：" + str(exc)

    async def close(self, runtime):
        # A normal shutdown must drain more than one batch; otherwise the
        # newest assignments behind 1,000 queued events disappear on restart.
        deadline = time.monotonic() + 3
        while self.queue and time.monotonic() < deadline:
            before = len(self.queue)
            await self.flush()
            if len(self.queue) >= before or self.history_error:
                break
        if self.queue:
            self.dropped += len(self.queue)
            self.queue.clear()
            self.queue_bytes = 0
            self.history_error = "关闭时存储不可用或排空超时，未持久化的记录已计入缺口"
        await self.snapshot(runtime)
        closed = True
        for db in (self.telemetry_reader, self.config_db, self.telemetry_db):
            if db:
                closed = await db.close() and closed
        if closed:
            self.lock.close()
        else:
            self.error = "数据库线程未退出，保留实例锁直到进程退出"
        return closed
