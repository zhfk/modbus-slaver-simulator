import asyncio
import contextlib
import json
import logging
import logging.handlers
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from .codec import decode, encode
from .errors import DomainError
from .excel import HeavyTasks
from .files import directory_bytes
from .models import Configuration, thermal_template, uid
from .modbus import ModbusService
from .paths import default_data_dir
from .runtime import Runtime
from .storage import MiB, Storage


class TemporaryFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # BackgroundTask only runs after successful transmission. An
            # interrupted download or invalid Range also owns this cleanup.
            Path(self.path).unlink(missing_ok=True)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        maximum = 22 * MiB
        try:
            if int(headers.get(b"content-length", b"0")) > maximum:
                response = JSONResponse(
                    {"message": "请求体超过 22MiB"}, status_code=413
                )
                return await response(scope, receive, send)
        except ValueError:
            return await JSONResponse({"message": "非法请求长度"}, status_code=400)(
                scope, receive, send
            )
        total = 0

        async def limited():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > maximum:
                raise DomainError("请求体超过 22MiB", 413)
            return message

        await self.app(scope, limited, send)


def create_app(data_dir=None, static_dir=None):
    runtime = Runtime()
    modbus = ModbusService(runtime)
    directory = Path(
        data_dir or os.environ.get("MODBUS_DATA_DIR", default_data_dir())
    ).resolve()
    static = Path(static_dir or Path(__file__).parent / "static")
    context = {
        "storage": None,
        "heavy": None,
        "tasks": [],
        "sessions": 0,
        "controls": 0,
        "shutdown": False,
        "started": time.time(),
    }
    logger = logging.getLogger("simulator")

    @asynccontextmanager
    async def lifespan(app):
        storage = Storage(directory)
        context["storage"] = storage
        handler = logging.handlers.RotatingFileHandler(
            directory / "application.log",
            maxBytes=10 * MiB,
            backupCount=4,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        await storage.open()
        runtime.apply(storage.config)
        await storage.restore(runtime)
        heavy = HeavyTasks(
            storage.tmp, storage.config.settings.temporary_budget_mb * MiB
        )
        context["heavy"] = heavy
        runtime.on_tick = lambda: storage.sample(runtime)
        runtime.on_event = lambda event: storage.enqueue("event", event)
        context["tasks"] = [
            asyncio.create_task(runtime.run(), name="runtime"),
            asyncio.create_task(storage.maintain(runtime), name="storage"),
        ]
        runtime.task = context["tasks"][0]
        for device in storage.config.devices:
            if device.auto_start and not storage.error:
                with contextlib.suppress(DomainError):
                    await modbus.start(device.id)
        logger.info("应用启动；配置版本 %s", storage.config.version)
        try:
            yield
        finally:
            context["shutdown"] = True
            for task in context["tasks"]:
                task.cancel()
            await asyncio.gather(*context["tasks"], return_exceptions=True)
            try:
                async with asyncio.timeout(15):
                    await modbus.close()
                    await heavy.close()
                    if not await storage.close(runtime):
                        logger.error(storage.error)
            except TimeoutError:
                logger.error("关闭超时，保留实例锁直到进程退出，按异常恢复处理")
            logger.removeHandler(handler)
            handler.close()

    app = FastAPI(title="Modbus 从机模拟器", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.state.runtime, app.state.modbus, app.state.context = runtime, modbus, context

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            {"message": exc.message, "details": exc.details}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def request_error(request, exc):
        return JSONResponse(
            {
                "message": "请求字段校验失败",
                "details": [
                    {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                    for e in exc.errors()
                ],
            },
            status_code=422,
        )

    def storage():
        return context["storage"]

    def configured(payload):
        try:
            return Configuration.model_validate(payload)
        except ValidationError as exc:
            raise DomainError(
                "配置校验失败",
                details=[
                    {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                    for e in exc.errors(include_input=False, include_url=False)
                ],
            ) from exc

    def guard():
        if context["shutdown"] or storage().config_pending:
            raise DomainError("配置变更或关闭进行中，请稍后刷新", 409)

    def check_changes(new):
        old = {d.id: d for d in storage().config.devices}
        incoming = {d.id: d for d in new.devices}
        for key, prior in old.items():
            active = runtime.get(key)
            if active.status in ("stopped", "fault"):
                continue
            replacement = incoming.get(key)
            if replacement is None:
                raise DomainError(f"请先停止设备：{prior.name}", 409)
            endpoint_fields = (
                "host",
                "port",
                "unit_id",
                "max_connections",
                "idle_seconds",
                "frame_seconds",
                "unknown_unit",
                "valid_ranges",
            )
            before = {p.id: p.layout() for p in prior.points}
            after = {p.id: p.layout() for p in replacement.points}
            if before != after or any(
                getattr(prior, f) != getattr(replacement, f) for f in endpoint_fields
            ):
                raise DomainError(f"映射或监听变更需要先停止设备：{prior.name}", 409)

    async def save(new):
        guard()
        return await storage().commit(new, runtime, lambda: check_changes(new))

    def device_view(device):
        endpoint = modbus.endpoints.get((device.config.host, device.config.port))
        return {
            "id": device.config.id,
            "name": device.config.name,
            "status": device.status,
            "paused": device.paused,
            "error": device.error,
            "host": device.config.host,
            "port": device.config.port,
            "unit_id": device.config.unit_id,
            "points": len(device.points),
            "connections": len(endpoint.clients) if endpoint else 0,
            "last_request": endpoint.last_request if endpoint else None,
            "version": device.version,
        }

    @app.get("/api/config")
    async def get_config():
        return storage().config.model_dump()

    @app.put("/api/config")
    async def put_config(payload: dict):
        result = await save(configured(payload))
        return result.model_dump()

    @app.post("/api/templates/thermal")
    async def template(payload: dict):
        current = storage().config.model_dump()
        if payload.get("version") != current["version"]:
            raise DomainError("配置版本已变化", 409)
        try:
            device = thermal_template(
                payload.get("name", "温控设备"),
                payload.get("host", "127.0.0.1"),
                payload.get("port", 1502),
                payload.get("unit_id", 1),
            )
        except (ValueError, TypeError, ValidationError) as exc:
            raise DomainError(str(exc)) from exc
        current["devices"].append(device.model_dump())
        await save(configured(current))
        return {"id": device.id, "version": storage().config.version}

    @app.get("/api/devices")
    async def get_devices():
        return [device_view(d) for d in runtime.devices.values()]

    @app.get("/api/devices/{key}/points")
    async def get_points(
        key: str,
        page: int = 1,
        size: int = 50,
        search: str = "",
        area: str = "",
        state: str = "",
        sort: str = "config",
    ):
        device = runtime.get(key)
        if not 1 <= size <= 100 or page < 1 or len(search) > 128:
            raise DomainError("分页／搜索参数无效")
        keys = [
            k
            for k, p in device.points.items()
            if (
                not search
                or search.lower() in p.name.lower()
                or search in str(p.address)
            )
            and (not area or p.area == area)
            and (not state or device.point_status(k) == state)
        ]
        if sort == "address":
            keys.sort(key=lambda k: (device.points[k].area, device.points[k].address))
        offset = (page - 1) * size
        return {
            "items": [device.view(k) for k in keys[offset : offset + size]],
            "total": len(keys),
            "version": device.version,
            "time": time.time(),
        }

    @app.post("/api/devices/{key}/actions/{action}")
    async def device_action(key: str, action: str):
        guard()
        if context["controls"] >= (256 if action in ("stop", "pause") else 244):
            raise DomainError("控制请求过多", 429)
        context["controls"] += 1
        try:
            device = runtime.get(key)
            if action == "start":
                await modbus.start(key)
            elif action == "stop":
                await modbus.stop(key)
            elif action in ("pause", "resume"):
                if device.status != "running":
                    raise DomainError("设备尚未运行", 409)
                device.paused = action == "pause"
                device.stamp = time.monotonic()
                runtime.event(action, key, "暂停策略" if device.paused else "恢复策略")
            elif action == "reset":
                device.reset()
                runtime.event(action, key, "恢复初始值")
            else:
                raise DomainError("操作不存在", 404)
            return device_view(device)
        finally:
            context["controls"] -= 1

    @app.post("/api/devices/{key}/assign")
    async def assign(key: str, payload: dict):
        guard()
        items = payload.get("items")
        if (
            not isinstance(items, list)
            or not items
            or any(
                not isinstance(i, dict)
                or set(i) != {"id", "value"}
                or not isinstance(i["id"], str)
                for i in items
            )
        ):
            raise DomainError("需要有效的点位／数值列表")
        device = runtime.get(key)
        device.assign(items)
        event = {
            "time": time.time(),
            "kind": "manual",
            "device": key,
            "points": [i["id"] for i in items[:100]],
            "count": len(items),
        }
        runtime.audit(event)
        return {
            "items": [device.view(i["id"]) for i in items],
            "version": device.version,
        }

    @app.post("/api/devices/{key}/preview-value")
    async def preview_value(key: str, payload: dict):
        device = runtime.get(key)
        if not isinstance(payload.get("id"), str):
            raise DomainError("需要有效的点位 ID")
        point = device.points.get(payload.get("id"))
        if point is None:
            raise DomainError("点位不存在", 404)
        try:
            raw = encode(point, payload.get("value"))
            return {
                "raw": raw,
                "value": decode(point, raw),
                "write_mode": point.write_mode,
            }
        except (ValueError, TypeError) as exc:
            raise DomainError(str(exc)) from exc

    @app.post("/api/devices/{key}/points/actions/{action}")
    async def point_action(key: str, action: str, payload: dict):
        guard()
        keys = payload.get("ids", [])
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 10000
            or any(not isinstance(k, str) for k in keys)
        ):
            raise DomainError("选择有效点位")
        device = runtime.get(key)
        device.control_points(keys, action)
        return {"version": device.version}

    @app.get("/api/devices/{key}/trends")
    async def trends(key: str, ids: str):
        return runtime.subscribe(key, ids.split(",") if ids else [])

    @app.get("/api/diagnostics")
    async def diagnostics(
        device: str = "", errors_only: bool = False, limit: int = 100
    ):
        if not 1 <= limit <= 1000:
            raise DomainError("结果数量无效")
        endpoint = None
        if device:
            selected = runtime.get(device).config
            endpoint = f"{selected.host}:{selected.port}"
        rows = [
            r
            for r in runtime.diagnostics.values()
            if (
                not device
                or r["device"] == device
                or (r["device"] is None and r["endpoint"] == endpoint)
            )
            and (not errors_only or r["error"])
        ]
        return {
            "items": rows[-limit:][::-1],
            "dropped": runtime.diagnostics.dropped,
            "events": runtime.events.values()[-100:][::-1],
        }

    @app.post("/api/capture")
    async def capture(payload: dict):
        guard()
        seconds = payload.get("seconds", 60)
        if (
            not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 <= seconds <= 600
        ):
            raise DomainError("捕获时间必须为 0～600 秒")
        if seconds and not storage().writable():
            raise DomainError("持久化不可用，不能开启报文捕获", 503)
        runtime.capture_until = time.monotonic() + seconds
        runtime.capture_bytes = 0
        return {"seconds": seconds, "limit": runtime.capture_limit}

    @app.get("/api/health/live")
    async def live():
        now = time.monotonic()
        runtime_alive = bool(
            runtime.task and not runtime.task.done() and now - runtime.progress < 5
        )
        tasks_alive = all(not t.done() for t in context["tasks"])
        storage_progress = all(
            not db
            or (
                db.thread.is_alive()
                and (not db.busy or now - db.busy_since < 15)
                and (db.queue.empty() or now - db.progress < 15)
            )
            for db in (storage().config_db, storage().telemetry_db)
        )
        healthy = runtime_alive and tasks_alive and storage_progress
        return JSONResponse(
            {
                "live": healthy,
                "progress": runtime.progress,
                "runtime": runtime_alive,
                "tasks": tasks_alive,
                "storage": storage_progress,
            },
            status_code=200 if healthy else 503,
        )

    @app.get("/api/health/ready")
    async def ready():
        valid = (
            not storage().error
            and not storage().config_pending
            and not context["shutdown"]
        )
        return JSONResponse(
            {
                "ready": valid,
                "version": storage().config.version,
                "storage_error": storage().error,
            },
            status_code=200 if valid else 503,
        )

    @app.get("/api/health")
    async def health():
        process = psutil.Process()
        return {
            "uptime": time.time() - context["started"],
            "rss": process.memory_info().rss,
            "cpu_seconds": sum(process.cpu_times()[:2]),
            "threads": process.num_threads(),
            "handles": process.num_handles() if os.name == "nt" else process.num_fds(),
            "tasks": len(asyncio.all_tasks()),
            "loop_delay_ms": runtime.loop_delay * 1000,
            "max_loop_delay_ms": runtime.max_loop_delay * 1000,
            "storage": {
                **storage().metrics_cache,
                "data_dir": str(directory),
                "queue_count": len(storage().queue),
                "queue_bytes": storage().queue_bytes,
                "dropped": storage().dropped,
                "error": storage().error,
                "history_error": storage().nonessential_errors(),
            },
            "endpoints": modbus.metrics(),
            "settings": storage().config.settings.model_dump(),
            "sessions": context["sessions"],
            "capture": {
                "remaining": max(0, runtime.capture_until - time.monotonic()),
                "bytes": runtime.capture_bytes,
                "limit": runtime.capture_limit,
            },
        }

    @app.get("/api/history")
    async def history(
        device: str,
        point: str,
        start: float,
        end: float,
        limit: int = 1000,
        after: int = 0,
    ):
        rows = await storage().history(device, point, start, end, limit, after)
        return {"items": rows, "next": rows[-1]["id"] if len(rows) == limit else None}

    @app.post("/api/import/preview")
    async def import_preview(
        file: UploadFile = File(...), mode: str = "update", target: str = ""
    ):
        guard()
        if mode not in ("new", "update", "replace") or not (
            file.filename or ""
        ).lower().endswith(".xlsx"):
            raise DomainError("仅支持 xlsx 文件及新增／更新／替换模式")
        token = uid()
        path = storage().tmp / f"{token}.upload.xlsx"
        total = 0
        try:
            with open(path, "wb") as output:
                while chunk := await file.read(65536):
                    total += len(chunk)
                    if total > 20 * MiB:
                        raise DomainError("文件超过 20MiB", 413)
                    used = directory_bytes(storage().tmp)
                    if (
                        used + len(chunk)
                        > storage().config.settings.temporary_budget_mb * MiB
                    ):
                        raise DomainError("临时文件预算不足", 507)
                    await asyncio.to_thread(output.write, chunk)
            result = await context["heavy"].run(
                "parse", {"path": str(path), "mode": mode}
            )
        finally:
            path.unlink(missing_ok=True)
            await file.close()
        if result.get("errors"):
            return result
        current = storage().config.model_dump()
        old = {d["id"]: d for d in current["devices"]}
        new = result["config"]["devices"]
        added = changed = deleted = 0
        if mode == "replace":
            if (
                not target
                or target not in old
                or len(new) != 1
                or new[0]["id"] != target
            ):
                raise DomainError(
                    "替换须明确选择一个设备，并使用相同设备 ID 的单设备配置"
                )
            existing = {p["id"] for p in old[target]["points"]}
            incoming = {p["id"] for p in new[0]["points"]}
            deleted, added, changed = (
                len(existing - incoming),
                len(incoming - existing),
                len(incoming & existing),
            )
            old[target] = new[0]
        else:
            for device in new:
                prior = old.get(device["id"])
                if prior is None:
                    old[device["id"]] = device
                    added += len(device["points"])
                elif mode == "new":
                    raise DomainError("新增模式遇到已存在的设备 ID")
                else:
                    points = {p["id"]: p for p in prior["points"]}
                    for point in device["points"]:
                        changed += int(point["id"] in points)
                        added += int(point["id"] not in points)
                        points[point["id"]] = point
                    old[device["id"]] = {**device, "points": list(points.values())}
        current["devices"] = list(old.values())
        current["settings"]["history_points"] = [
            key
            for key in current["settings"]["history_points"]
            if any(key == p["id"] for d in current["devices"] for p in d["points"])
        ]
        candidate = configured(current)
        # Previews are stateless: the caller must return the exact validated
        # candidate and configuration version. Application validates it again.
        needs_stop = []
        try:
            check_changes(candidate)
        except DomainError as exc:
            needs_stop.append(exc.message)
        return {
            "errors": [],
            "config": candidate.model_dump(),
            "added": added,
            "changed": changed,
            "deleted": deleted,
            "needs_stop": needs_stop,
            "version": current["version"],
        }

    @app.post("/api/import/apply")
    async def import_apply(payload: dict):
        result = await save(configured(payload))
        return {"version": result.version}

    @app.get("/api/export/{kind}")
    async def export(
        kind: str, device: str = "", ids: str = "", start: float = 0, end: float = 0
    ):
        if kind not in ("config", "snapshot", "history", "template"):
            raise DomainError("导出类型不存在", 404)
        selected = set(ids.split(",")) if ids else None
        config = storage().config.model_dump()
        devices = [d for d in config["devices"] if not device or d["id"] == device]
        if device and not devices:
            raise DomainError("设备不存在", 404)
        if selected:
            if not selected.issubset({p["id"] for d in devices for p in d["points"]}):
                raise DomainError("导出选择包含不存在点位")
            for d in devices:
                # Dependencies must travel with a selected point for a valid
                # importable configuration; resolve the transitive closure.
                keys, points = set(selected), {p["id"]: p for p in d["points"]}
                if kind == "config":
                    todo = list(keys & points.keys())
                    while todo:
                        key = todo.pop()
                        for dep in points[key]["strategy"]["dependencies"]:
                            if dep not in keys:
                                keys.add(dep)
                                todo.append(dep)
                d["points"] = [p for p in d["points"] if p["id"] in keys]
        rows = []
        if kind == "history":
            for d in devices:
                for p in d["points"]:
                    cursor = 0
                    while True:
                        batch = await storage().history(
                            d["id"], p["id"], start, end, 1000, cursor
                        )
                        rows.extend(
                            {**r, "device": d["id"], "point": p["id"]} for r in batch
                        )
                        if len(rows) > 10000:
                            raise DomainError("导出超过 10000 条，请缩小时间范围", 413)
                        if len(batch) < 1000:
                            break
                        cursor = batch[-1]["id"]
        elif kind == "snapshot":
            sampled = time.time()
            rows = [
                {
                    "time": sampled,
                    "device": d["id"],
                    "point": p["id"],
                    **runtime.get(d["id"]).view(p["id"]),
                }
                for d in devices
                for p in d["points"]
            ]
        if kind == "template":
            devices = [thermal_template().model_dump()]
        config["settings"]["history_points"] = [
            key
            for key in config["settings"]["history_points"]
            if key in {p["id"] for d in devices for p in d["points"]}
        ]
        payload = {
            "kind": "config" if kind in ("config", "template") else kind,
            "devices": devices,
            "settings": config["settings"],
            "rows": rows,
        }
        result = await context["heavy"].run("export", payload)
        if result.get("errors"):
            raise DomainError("导出失败", details=result["errors"])
        path = Path(result["path"])
        return TemporaryFileResponse(
            path,
            filename=f"modbus-{kind}.xlsx",
        )

    @app.websocket("/api/live")
    async def websocket(ws: WebSocket):
        if context["sessions"] >= 4:
            await ws.close(code=1013, reason="实时会话已达上限")
            return
        await ws.accept()
        context["sessions"] += 1
        try:
            # Pull complete pages rather than queueing incremental deltas.
            # At most one response is in flight; slow clients time out.
            while True:
                query = await asyncio.wait_for(ws.receive_json(), 30)
                if not isinstance(query, dict):
                    await ws.close(code=1008, reason="订阅格式无效")
                    break
                device = runtime.get(str(query.get("device", "")))
                keys = query.get("ids", [])
                if (
                    not isinstance(keys, list)
                    or len(keys) > 100
                    or any(
                        not isinstance(k, str) or k not in device.points for k in keys
                    )
                ):
                    await ws.close(code=1008, reason="点位订阅无效")
                    break
                payload = {
                    "time": time.time(),
                    "version": device.version,
                    "complete": True,
                    "device": device_view(device),
                    "items": [device.view(k) for k in keys],
                }
                if len(json.dumps(payload).encode()) > MiB:
                    await ws.close(code=1009, reason="快照过大，请减少订阅")
                    break
                await asyncio.wait_for(ws.send_json(payload), 5)
        except (WebSocketDisconnect, TimeoutError, DomainError, ValueError, TypeError):
            pass
        finally:
            context["sessions"] -= 1
            with contextlib.suppress(RuntimeError):
                await ws.close()

    @app.get("/{path:path}")
    async def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"message": "接口不存在"}, status_code=404)
        root = static.resolve()
        requested = (root / path).resolve()
        if not requested.is_relative_to(root):
            return JSONResponse({"message": "路径无效"}, status_code=404)
        if requested.is_file():
            return FileResponse(requested)
        if path.startswith(("assets/", "fonts/")) or "." in Path(path).name:
            return JSONResponse({"message": "资源不存在"}, status_code=404)
        if (static / "index.html").is_file():
            return FileResponse(
                static / "index.html", headers={"Cache-Control": "no-cache"}
            )
        return JSONResponse(
            {"message": "前端未构建，请执行 npm run build"}, status_code=503
        )

    return app
