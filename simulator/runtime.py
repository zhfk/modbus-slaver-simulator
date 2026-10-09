import ast
import asyncio
import hashlib
import json
import math
import random
import time
from array import array
from collections import deque
from dataclasses import dataclass, field

from .codec import decode, encode, safe_number, width
from .errors import DomainError, ProtocolError
from .models import Device

AREAS = ("coil", "discrete", "holding", "input")


class ByteRing:
    def __init__(self, count, budget):
        self.rows = deque()
        self.count, self.budget = count, budget
        self.bytes = self.dropped = 0

    def append(self, item):
        size = len(json.dumps(item, ensure_ascii=False, allow_nan=False).encode())
        if size > self.budget:
            self.dropped += 1
            return
        while self.rows and (
            len(self.rows) >= self.count or self.bytes + size > self.budget
        ):
            _, old_size = self.rows.popleft()
            self.bytes -= old_size
            self.dropped += 1
        self.rows.append((item, size))
        self.bytes += size

    def latest(self):
        return self.rows[-1][0] if self.rows else None

    def values(self):
        return [row for row, _ in self.rows]


@dataclass
class PointState:
    rng: random.Random
    elapsed: float = 0
    due: float = 0
    last_clock: float = 0
    hold: bool = False
    override_until: float = 0
    enabled: bool = True
    error: str = ""
    source: str = "initial"
    changed: float = field(default_factory=time.time)
    previous: float | bool | None = None
    skipped: int = 0
    signal: float | None = None
    generated_elapsed: float = 0


class CycleValues(dict):
    def __init__(self, device):
        super().__init__()
        self.device = device

    def __missing__(self, key):
        value = self.device.value(key)
        self[key] = value
        return value


class DeviceRuntime:
    def __init__(self, config: Device, previous=None):
        self.config = config
        self.values = {area: array("H", [0]) * 65536 for area in AREAS}
        self.owners = {area: {} for area in AREAS}
        self.points = {p.id: p for p in config.points}
        self.states = {}
        self.status, self.error = "stopped", ""
        self.paused = False
        self.clock, self.stamp, self.progress = 0.0, time.monotonic(), time.monotonic()
        self.version = previous.version + 1 if previous else 0
        self.audit_callback = None
        self.trends = {}
        self.last_trend = 0
        for p in config.points:
            state = PointState(
                random.Random(p.strategy.seed), enabled=p.strategy.enabled
            )
            data = encode(p, p.initial)
            if (
                previous
                and p.id in previous.points
                and previous.points[p.id].layout() == p.layout()
            ):
                data = previous.raw(p.id)
                state = previous.states[p.id]
                state.enabled = p.strategy.enabled
                if p.strategy != previous.points[p.id].strategy:
                    state.rng = random.Random(p.strategy.seed)
                    state.error = ""
                self.clock = previous.clock
            self.states[p.id] = state
            self.values[p.area][p.address : p.address + width(p.type)] = array(
                "H", data
            )
            for a in range(p.address, p.address + width(p.type)):
                self.owners[p.area][a] = p.id
        if previous:
            self.status, self.paused = previous.status, previous.paused
        self.order = self._order()
        self.expressions = {
            p.id: compile(
                ast.parse(p.strategy.params.get("expression", "0"), mode="eval"),
                "<strategy>",
                "eval",
            )
            for p in config.points
            if p.strategy.kind == "expression"
        }

    def _order(self):
        result, todo = (
            [],
            {p.id: set(p.strategy.dependencies) for p in self.config.points},
        )
        dependents = {key: [] for key in todo}
        for key, deps in todo.items():
            for dep in deps:
                dependents[dep].append(key)
        ready = deque(key for key, deps in todo.items() if not deps)
        while ready:
            key = ready.popleft()
            result.append(key)
            for child in dependents[key]:
                todo[child].discard(key)
                if not todo[child]:
                    ready.append(child)
        return result

    def raw(self, key):
        p = self.points[key]
        return list(self.values[p.area][p.address : p.address + width(p.type)])

    def value(self, key):
        return decode(self.points[key], self.raw(key))

    def set_raw(self, key, words, source="strategy", write=False):
        p, st = self.points[key], self.states[key]
        old = self.value(key)
        self.values[p.area][p.address : p.address + len(words)] = array("H", words)
        st.previous, st.changed, st.source = safe_number(old), time.time(), source
        if write:
            st.signal = safe_number(self.value(key))
            st.hold = p.write_mode == "hold"
            st.override_until = (
                time.monotonic() + p.override_seconds
                if p.write_mode == "temporary"
                else 0
            )
        st.error = ""
        self.version += 1

    def assign(self, items, source="manual"):
        if len(items) > 10000 or len({x["id"] for x in items}) != len(items):
            raise DomainError("赋值列表重复或超过上限")
        prepared = []
        for item in items:
            if item["id"] not in self.points:
                raise DomainError("点位不存在", 404)
            try:
                prepared.append(
                    (item["id"], encode(self.points[item["id"]], item["value"]))
                )
            except (ValueError, TypeError) as exc:
                raise DomainError(str(exc), details={"point": item["id"]}) from exc
        for key, words in prepared:
            self.set_raw(key, words, source, write=True)

    def check_range(self, area, address, count):
        ranges = self.config.valid_ranges.get(area)
        if address < 0 or address + count > 65536:
            raise ProtocolError(2)
        if ranges is not None:
            for a in range(address, address + count):
                if not any(start <= a <= end for start, end in ranges):
                    raise ProtocolError(2)

    def read(self, area, address, count):
        self.check_range(area, address, count)
        if self.config.missing_address == "exception" and any(
            a not in self.owners[area] for a in range(address, address + count)
        ):
            raise ProtocolError(2)
        return list(self.values[area][address : address + count])

    def write(self, area, address, words, source):
        self.check_range(area, address, len(words))
        keys = set()
        for a in range(address, address + len(words)):
            key = self.owners[area].get(a)
            if not key or not self.points[key].writable:
                raise ProtocolError(2)
            keys.add(key)
        old = {key: safe_number(self.value(key)) for key in keys}
        self.values[area][address : address + len(words)] = array("H", words)
        for key in keys:
            p, st = self.points[key], self.states[key]
            st.previous, st.changed, st.source, st.error = (
                old[key],
                time.time(),
                source,
                "",
            )
            st.signal = safe_number(self.value(key))
            st.hold = p.write_mode == "hold"
            st.override_until = (
                time.monotonic() + p.override_seconds
                if p.write_mode == "temporary"
                else 0
            )
        self.version += 1
        if self.audit_callback:
            self.audit_callback(
                {
                    "time": time.time(),
                    "kind": "external_write",
                    "device": self.config.id,
                    "area": area,
                    "address": address,
                    "raw": words,
                    "changes": [
                        {
                            "id": key,
                            "before": old[key],
                            "after": safe_number(self.value(key)),
                        }
                        for key in sorted(keys)
                    ],
                }
            )

    def control_points(self, keys, action):
        if len(set(keys)) != len(keys) or any(key not in self.points for key in keys):
            raise DomainError("点位选择无效")
        for key in keys:
            st = self.states[key]
            if action == "resume":
                st.hold, st.override_until, st.error = False, 0, ""
            elif action == "pause":
                st.hold = True
            else:
                raise DomainError("不支持的点位操作")
        self.version += 1

    def reset(self):
        if self.status not in ("stopped", "fault"):
            raise DomainError("请先停止设备", 409)
        fresh = DeviceRuntime(self.config)
        self.values, self.states, self.clock = fresh.values, fresh.states, 0
        self.trends.clear()
        self.last_trend, self.stamp = 0, fresh.stamp
        self.paused, self.error = False, ""
        self.version += 1

    def strategy_value(self, key, values, dt):
        p, st = self.points[key], self.states[key]
        kind, par, t = p.strategy.kind, p.strategy.params, st.elapsed
        deps = [values[d] for d in p.strategy.dependencies]
        current = values[key]
        continuous = st.signal if st.signal is not None else current

        def number(k, default):
            return float(par.get(k, default))

        if kind == "fixed":
            return par.get("value", p.initial)
        if kind == "random":
            return st.rng.uniform(number("min", 0), number("max", 100))
        if kind in ("sine", "noise"):
            base = par.get("base", "sine") if kind == "noise" else "sine"
            if base == "fixed":
                v = number("value", 50)
            elif base == "random":
                v = st.rng.uniform(number("min", 0), number("max", 100))
            elif base == "ramp":
                duration = number("duration", 60)
                position = t % duration if par.get("loop", True) else min(t, duration)
                v = (
                    number("start", 0)
                    + (number("end", 100) - number("start", 0)) * position / duration
                )
            else:
                v = number("mean", 50) + number("amplitude", 10) * math.sin(
                    2 * math.pi * t / number("period", 60) + number("phase", 0)
                )
            noise = (
                st.rng.gauss(0, number("noise", 1))
                if par.get("distribution") == "normal"
                else st.rng.uniform(-number("noise", 1), number("noise", 1))
            )
            return v + noise if kind == "noise" else v
        if kind == "ramp":
            duration = number("duration", 60)
            fraction = (
                t % duration if par.get("loop", True) else min(t, duration)
            ) / duration
            return (
                number("start", 0)
                + (number("end", 100) - number("start", 0)) * fraction
            )
        if kind == "walk":
            return max(
                number("min", 0),
                min(
                    number("max", 100),
                    current + st.rng.uniform(-number("step", 1), number("step", 1)),
                ),
            )
        if kind == "sequence":
            total = sum(float(row[0]) for row in par["values"])
            position = t % total if par.get("loop", True) else min(t, total - 1e-9)
            for duration, value in par["values"]:
                if position < float(duration):
                    return value
                position -= float(duration)
        if kind == "replay":
            rows = par["values"]
            duration = float(rows[-1][0])
            position = t * number("speed", 1)
            if duration > 0 and par.get("loop", True):
                position %= duration
            value = rows[0][1]
            for at, sample in rows:
                if float(at) > position:
                    break
                value = sample
            return value
        if kind == "link":
            target = deps[0] * number("gain", 1) + number("offset", 0)
            return continuous + (target - continuous) * (
                1 - math.exp(-dt / number("response_time", 1))
            )
        if kind == "expression":
            return eval(
                self.expressions[key],
                {"__builtins__": {}},
                {"t": t, **{f"x{i}": value for i, value in enumerate(deps)}},
            )
        if kind == "thermal":
            target = deps[1] if deps[0] else number("ambient", 25)
            return (
                continuous
                + (target - continuous)
                * (1 - math.exp(-dt / number("response_time", 10)))
                + st.rng.uniform(-number("noise", 0), number("noise", 0))
            )
        if kind == "alarm":
            threshold, hysteresis = number("threshold", 80), number("hysteresis", 2)
            return (
                deps[0] >= threshold
                if not current
                else deps[0] > threshold - hysteresis
            )
        return current

    def tick(self, now):
        delta = max(0, now - self.stamp)
        self.stamp = now
        self.progress = now
        if self.status != "running" or self.paused or self.config.faults.freeze:
            self.sample_trends(now)
            return
        self.clock += delta
        staged, current = [], CycleValues(self)
        for key in self.order:
            p, st = self.points[key], self.states[key]
            step = self.clock - st.last_clock
            st.last_clock = self.clock
            if (
                st.hold
                or st.override_until > now
                or not st.enabled
                or p.strategy.kind == "none"
            ):
                continue
            st.elapsed += step
            if self.clock < st.due:
                continue
            st.skipped += max(0, int((self.clock - st.due) / p.strategy.interval) - 1)
            st.due = self.clock + p.strategy.interval
            try:
                value = self.strategy_value(
                    key, current, max(0.000001, st.elapsed - st.generated_elapsed)
                )
                words = encode(p, value)
                current[key] = decode(p, words)
                st.signal = float(value)
                st.generated_elapsed = st.elapsed
                staged.append((key, words))
            except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
                st.error = str(exc)[:256]
                st.hold = True
        for key, words in staged:
            self.set_raw(key, words)
        self.sample_trends(now)

    def sample_trends(self, now):
        if not self.trends or now - self.last_trend < 1:
            return
        self.last_trend = now
        at = time.time()
        for key, cache in self.trends.items():
            if cache and at < cache[-1][0]:
                cache.clear()  # Start a new segment if the system clock moves back.
            cache.append((at, safe_number(self.value(key))))

    def point_status(self, key):
        st, p = self.states[key], self.points[key]
        if st.error:
            return "error"
        if st.hold:
            return "held"
        if st.override_until > time.monotonic():
            return "temporary"
        if self.status != "running":
            return "stopped"
        if self.paused or self.config.faults.freeze:
            return "paused"
        return "running" if p.strategy.kind != "none" and st.enabled else "idle"

    def view(self, key):
        p, st, value = self.points[key], self.states[key], self.value(key)
        return {
            **p.model_dump(),
            "value": safe_number(value),
            "quality": "good"
            if isinstance(value, bool) or math.isfinite(value)
            else "non_finite",
            "raw": self.raw(key),
            "state": self.point_status(key),
            "source": st.source,
            "changed": st.changed,
            "previous": st.previous,
            "error": st.error,
            "remaining": max(0, st.override_until - time.monotonic()),
            "skipped": st.skipped,
        }

    def fingerprint(self, key):
        return hashlib.sha256(
            json.dumps(self.points[key].layout()).encode()
        ).hexdigest()

    def snapshot(self):
        return {
            "clock": self.clock,
            "paused": self.paused,
            "points": {
                key: {
                    "fingerprint": self.fingerprint(key),
                    "raw": self.raw(key),
                    "elapsed": st.elapsed,
                    "hold": st.hold,
                    "remaining": max(0, st.override_until - time.monotonic()),
                    "rng": st.rng.getstate(),
                    "enabled": st.enabled,
                    "signal": st.signal,
                    "generated_elapsed": st.generated_elapsed,
                }
                for key, st in self.states.items()
            },
        }

    def restore_snapshot(self, saved):
        restored = skipped = 0
        self.clock = float(saved.get("clock", 0))
        self.paused = bool(saved.get("paused", False))
        for state in self.states.values():
            state.last_clock = self.clock
        for key, item in saved.get("points", {}).items():
            if (
                key not in self.points
                or self.points[key].restore != "snapshot"
                or item.get("fingerprint") != self.fingerprint(key)
            ):
                skipped += 1
                continue
            p, st = self.points[key], self.states[key]
            words = item.get("raw", [])
            if len(words) != width(p.type) or any(
                type(w) is not int or not 0 <= w <= 65535 for w in words
            ):
                skipped += 1
                continue
            self.set_raw(key, words, "snapshot")
            st.elapsed, st.last_clock, st.due = (
                float(item["elapsed"]),
                self.clock,
                self.clock,
            )
            st.hold, st.enabled = bool(item["hold"]), bool(item.get("enabled", True))
            st.override_until = time.monotonic() + max(
                0, min(float(item.get("remaining", 0)), p.override_seconds)
            )
            st.signal = item.get("signal")
            st.generated_elapsed = float(item.get("generated_elapsed", st.elapsed))
            state = item.get("rng")
            if state:
                st.rng.setstate((state[0], tuple(state[1]), state[2]))
            restored += 1
        return {"restored": restored, "skipped": skipped}


class Runtime:
    def __init__(self):
        self.devices = {}
        self.diagnostics = ByteRing(1000, 4 * 1024 * 1024)
        self.events = ByteRing(1000, 1024 * 1024)
        self.task = None
        self.progress = time.monotonic()
        self.loop_delay = 0
        self.max_loop_delay = 0
        self.on_tick = None
        self.on_event = None
        self.capture_until = 0
        self.capture_bytes = 0
        self.capture_limit = 4 * 1024 * 1024
        self.trend_touch = {}
        self.last_trend_cleanup = 0

    def apply(self, config):
        previous = self.devices
        self.devices = {
            d.id: DeviceRuntime(d, previous.get(d.id)) for d in config.devices
        }
        for device in self.devices.values():
            device.audit_callback = self.audit
        self.trend_touch = {
            key: stamp
            for key, stamp in self.trend_touch.items()
            if any(key in d.points for d in self.devices.values())
        }

    async def run(self):
        expected = time.monotonic()
        while True:
            now = time.monotonic()
            self.loop_delay = max(0, now - expected)
            self.max_loop_delay = max(self.max_loop_delay, self.loop_delay)
            self.progress = now
            if now - self.last_trend_cleanup >= 1:
                self.expire_trends(now)
            for device in list(self.devices.values()):
                device.tick(time.monotonic())
                await asyncio.sleep(0)
            if self.on_tick:
                self.on_tick()
            expected = time.monotonic() + 0.01
            await asyncio.sleep(0.01)

    def get(self, key):
        if key not in self.devices:
            raise DomainError("设备不存在", 404)
        return self.devices[key]

    def audit(self, event):
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    def event(self, kind, device, detail):
        self.audit(
            {
                "time": time.time(),
                "kind": kind,
                "device": device,
                "detail": str(detail)[:1024],
            }
        )

    def capture(self, event):
        if time.monotonic() < self.capture_until:
            size = len(json.dumps(event).encode())
            if self.capture_bytes + size <= self.capture_limit:
                self.capture_bytes += size
                self.audit({**event, "kind": "packet_capture"})
            else:
                self.capture_until = 0
                self.event("capture_limit", "", "捕获到达容量上限，已停止")

    def expire_trends(self, now):
        self.last_trend_cleanup = now
        for rt in self.devices.values():
            for key in list(rt.trends):
                if now - self.trend_touch.get(key, 0) > 90:
                    rt.trends.pop(key)
                    self.trend_touch.pop(key, None)

    def subscribe(self, device_id, keys):
        device = self.get(device_id)
        if len(keys) > 4 or any(k not in device.points for k in keys):
            raise DomainError("趋势最多选择 4 个有效点位")
        now = time.monotonic()
        self.expire_trends(now)
        total = sum(len(d.trends) for d in self.devices.values())
        if total + sum(k not in device.trends for k in keys) > 256:
            raise DomainError("趋势缓存已达上限", 429)
        had_trends = bool(device.trends)
        at = time.time()
        for key in keys:
            cache = device.trends.setdefault(key, deque(maxlen=600))
            if not cache:
                cache.append((at, safe_number(device.value(key))))
            self.trend_touch[key] = now
        if keys and not had_trends:
            device.last_trend = now
        return {key: list(device.trends[key]) for key in keys}
