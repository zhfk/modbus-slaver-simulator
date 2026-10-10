import ast
import ipaddress
import math
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator
from .codec import encode, width


def uid():
    return uuid.uuid4().hex


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_default=True
    )


class Strategy(StrictModel):
    kind: Literal[
        "none",
        "fixed",
        "random",
        "sine",
        "ramp",
        "walk",
        "noise",
        "sequence",
        "link",
        "expression",
        "replay",
        "thermal",
        "alarm",
    ] = "none"
    enabled: bool = True
    interval: float = Field(default=1, ge=0.01, le=86400)
    seed: int = Field(default=1, ge=0, le=2**32 - 1)
    params: dict = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_params(self):
        p = self.params

        def valid_json(value, depth=0):
            if depth > 8:
                raise ValueError("策略参数嵌套过深")
            if isinstance(value, dict):
                for k, v in value.items():
                    if not isinstance(k, str) or len(k) > 128:
                        raise ValueError("策略参数键无效")
                    valid_json(v, depth + 1)
            elif isinstance(value, list):
                for v in value:
                    valid_json(v, depth + 1)
            elif isinstance(value, (float, int)):
                if not math.isfinite(value) or abs(value) > 1e100:
                    raise ValueError("策略参数必须为有限数值")
            elif not isinstance(value, (str, bool, type(None))):
                raise ValueError("策略参数类型无效")

        valid_json(p)
        if len(str(p)) > 65536 or len(p) > 32:
            raise ValueError("策略参数过大")

        def number(key, default=0):
            try:
                v = float(p.get(key, default))
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"{key} 必须为有限数值") from exc
            if not math.isfinite(v):
                raise ValueError(f"{key} 必须为有限数值")
            return v

        for key in (
            "min",
            "max",
            "mean",
            "value",
            "amplitude",
            "phase",
            "start",
            "end",
            "step",
            "noise",
            "threshold",
            "hysteresis",
            "ambient",
            "duration",
            "period",
            "response_time",
            "gain",
            "speed",
        ):
            if key in p:
                number(key)
        if number("min", 0) > number("max", 100):
            raise ValueError("最小值不能大于最大值")
        for key in ("period", "duration", "response_time"):
            if key in p and number(key) <= 0:
                raise ValueError(f"{key} 必须大于零")
        for key in ("amplitude", "step", "noise", "hysteresis"):
            if key in p and number(key) < 0:
                raise ValueError(f"{key} 不能为负数")
        if self.kind == "noise" and p.get("base", "sine") not in (
            "sine",
            "fixed",
            "ramp",
            "random",
        ):
            raise ValueError("带噪声策略基础信号仅支持 sine、fixed、ramp、random")
        if self.kind == "noise" and p.get("distribution", "uniform") not in (
            "uniform",
            "normal",
        ):
            raise ValueError("噪声分布仅支持 uniform、normal")
        if self.kind == "expression":
            try:
                tree = ast.parse(str(p.get("expression", "0")), mode="eval")
            except SyntaxError as exc:
                raise ValueError("表达式语法无效") from exc
            allowed = (
                ast.Expression,
                ast.BinOp,
                ast.UnaryOp,
                ast.Name,
                ast.Load,
                ast.Constant,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.USub,
                ast.UAdd,
                ast.Mod,
            )
            nodes = list(ast.walk(tree))
            if len(nodes) > 64 or any(not isinstance(n, allowed) for n in nodes):
                raise ValueError("表达式仅允许常数、x0…x31、t 和基本四则运算")
            if any(
                isinstance(n, ast.Name)
                and n.id not in ["t"] + [f"x{i}" for i in range(len(self.dependencies))]
                for n in nodes
            ):
                raise ValueError("表达式含未知变量")
            if any(
                isinstance(n, ast.Constant)
                and (not isinstance(n.value, (int, float)) or abs(n.value) > 1e100)
                for n in nodes
            ):
                raise ValueError("表达式常数非法")
        if self.kind in ("link", "alarm") and not self.dependencies:
            raise ValueError("联动策略需要依赖点位")
        if self.kind == "thermal" and (
            len(self.dependencies) != 2 or len(set(self.dependencies)) != 2
        ):
            raise ValueError("温控策略需要两个不同依赖：启动命令、目标温度（按此顺序）")
        if self.kind in ("sequence", "replay"):
            rows = p.get("values", [])
            if not isinstance(rows, list) or not rows or len(rows) > 2000:
                raise ValueError("序列／回放需要 1～2000 个样本")
            prev = -1
            for row in rows:
                if not isinstance(row, list) or len(row) != 2:
                    raise ValueError("样本格式为 [时间或持续秒数, 值]")
                try:
                    a, b = map(float, row)
                except (ValueError, TypeError, OverflowError) as exc:
                    raise ValueError("样本必须为有限数值") from exc
                if not math.isfinite(a) or not math.isfinite(b) or a < 0:
                    raise ValueError("样本必须有限且时间非负")
                if self.kind == "sequence" and a <= 0:
                    raise ValueError("状态持续时间必须大于零")
                if self.kind == "replay" and a <= prev:
                    raise ValueError("回放时间须严格递增")
                prev = a
            if self.kind == "replay" and float(p.get("speed", 1)) <= 0:
                raise ValueError("回放速度必须大于零")
        return self


class Point(StrictModel):
    id: str = Field(default_factory=uid, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    group: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=1024)
    area: Literal["coil", "discrete", "holding", "input"] = "holding"
    address: int = Field(default=0, ge=0, le=65535)
    type: Literal[
        "Bool", "Int16", "UInt16", "Int32", "UInt32", "Float32", "Float64"
    ] = "UInt16"
    byte_order: Literal["big", "little"] = "big"
    word_order: Literal["big", "little"] = "big"
    scale: float = 1
    offset: float = 0
    unit: str = Field(default="", max_length=32)
    precision: int = Field(
        default_factory=lambda data: (
            2 if data.get("type") in ("Float32", "Float64") else 0
        ),
        ge=0,
        le=10,
        description="整数默认 0 位小数，浮点数默认 2 位小数",
    )
    initial: float | bool = 0
    writable: bool = False
    strategy: Strategy = Field(default_factory=Strategy)
    write_mode: Literal["hold", "temporary", "continue", "control"] = "hold"
    override_seconds: float = Field(default=10, gt=0, le=86400)
    restore: Literal["initial", "snapshot"] = "snapshot"

    @model_validator(mode="after")
    def validate_point(self):
        if self.type not in ("Float32", "Float64"):
            self.precision = 0
        if self.scale == 0:
            raise ValueError("倍率不能为零")
        if (self.area in ("coil", "discrete")) != (self.type == "Bool"):
            raise ValueError("位数据区只允许 Bool，寄存器数据区不允许 Bool")
        if self.type == "Bool" and (self.scale != 1 or self.offset != 0):
            raise ValueError("Bool 不使用倍率和偏移")
        if self.type == "Bool" and self.strategy.kind == "random":
            lo, hi = (
                self.strategy.params.get("min", 0),
                self.strategy.params.get("max", 1),
            )
            if lo not in (0, 1) or hi not in (0, 1) or lo > hi:
                raise ValueError(
                    "Bool 均匀随机的最小值和最大值只能为 0／1，且最小值不能大于最大值"
                )
        if self.area in ("discrete", "input") and self.writable:
            raise ValueError("输入数据区对主机只读")
        if self.address + width(self.type) > 65536:
            raise ValueError("占用范围超过地址上限")
        if self.write_mode == "control" and self.strategy.kind != "none":
            raise ValueError("控制输入不允许周期生成策略")
        encode(self, self.initial)
        return self

    def layout(self):
        return (
            self.area,
            self.address,
            self.type,
            self.byte_order,
            self.word_order,
            float(self.scale),
            float(self.offset),
        )


class Faults(StrictModel):
    delay_ms: int = Field(default=0, ge=0, le=5000)
    timeout_rate: float = Field(default=0, ge=0, le=1)
    disconnect_rate: float = Field(default=0, ge=0, le=1)
    freeze: bool = False


class Identity(StrictModel):
    vendor: str = Field(default="Modbus Lab", max_length=64)
    product: str = Field(default="TCP Simulator", max_length=64)
    revision: str = Field(default="0.1.0", max_length=64)

    @model_validator(mode="after")
    def limits(self):
        if any(
            len(v.encode("utf-8")) > 64
            for v in (self.vendor, self.product, self.revision)
        ):
            raise ValueError("设备身份每个字段的 UTF-8 编码不能超过 64 字节")
        return self


class Device(StrictModel):
    id: str = Field(default_factory=uid, min_length=1, max_length=64)
    name: str = Field(default="新设备", min_length=1, max_length=128)
    description: str = Field(default="", max_length=1024)
    host: str = "127.0.0.1"
    port: int = Field(default=1502, ge=1, le=65535)
    unit_id: int = Field(default=1, ge=0, le=255)
    functions: list[int] = Field(
        default_factory=lambda: [1, 2, 3, 4, 5, 6, 15, 16], max_length=10
    )
    identity: Identity = Field(default_factory=Identity)
    read_identity: bool = False
    valid_ranges: dict[str, list[list[int]]] = Field(default_factory=dict)
    missing_address: Literal["exception", "zero"] = "exception"
    unknown_unit: Literal["silence", "exception"] = "silence"
    max_connections: int = Field(default=32, ge=1, le=32)
    idle_seconds: float = Field(default=300, ge=1, le=86400)
    frame_seconds: float = Field(default=5, ge=0.05, le=30)
    auto_start: bool = False
    faults: Faults = Field(default_factory=Faults)
    points: list[Point] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def validate_device(self):
        ipaddress.ip_address(self.host)
        if (
            not self.functions
            or len(set(self.functions)) != len(self.functions)
            or any(f not in [1, 2, 3, 4, 5, 6, 15, 16, 22, 23] for f in self.functions)
        ):
            raise ValueError("功能码必须是支持列表中的唯一项")
        for area, ranges in self.valid_ranges.items():
            if area not in ("coil", "discrete", "holding", "input") or len(ranges) > 32:
                raise ValueError("有效地址范围数据区非法或范围过多")
            previous_end = -1
            for interval in ranges:
                if (
                    len(interval) != 2
                    or any(type(a) is not int for a in interval)
                    or not 0 <= interval[0] <= interval[1] <= 65535
                    or interval[0] <= previous_end
                ):
                    raise ValueError(
                        "有效地址范围须按起点排序、互不重叠且在 0～65535 内"
                    )
                previous_end = interval[1]
        ids, occupied = set(), set()
        for p in self.points:
            if p.id in ids:
                raise ValueError("点位 ID 重复")
            ids.add(p.id)
            ranges = self.valid_ranges.get(p.area)
            if ranges is not None and not any(
                start <= p.address and p.address + width(p.type) - 1 <= end
                for start, end in ranges
            ):
                raise ValueError(f"点位 {p.name} 超出有效地址范围")
            for address in range(p.address, p.address + width(p.type)):
                key = (p.area, address)
                if key in occupied:
                    raise ValueError(f"点位 {p.name} 的地址范围重叠")
                occupied.add(key)
        from collections import deque

        by_id = {p.id: p for p in self.points}
        deps = {p.id: set(p.strategy.dependencies) for p in self.points}
        downstream = {key: [] for key in by_id}
        for key, links in deps.items():
            for dep in links:
                if dep not in by_id:
                    raise ValueError(f"依赖点位不存在：{dep}")
                downstream[dep].append(key)
        ready, visited = deque(k for k, links in deps.items() if not links), 0
        while ready:
            key = ready.popleft()
            visited += 1
            for child in downstream[key]:
                deps[child].remove(key)
                if not deps[child]:
                    ready.append(child)
        if visited != len(by_id):
            raise ValueError("点位联动存在循环依赖")
        return self


class Settings(StrictModel):
    history_enabled: bool = False
    history_points: list[str] = Field(default_factory=list, max_length=10000)
    history_interval: float = Field(default=1, ge=0.1, le=86400)
    retention_days: float = Field(default=7, gt=0, le=3650)
    telemetry_budget_mb: int = Field(default=1024, ge=1, le=65536)
    config_budget_mb: int = Field(default=64, ge=1, le=1024)
    backup_budget_mb: int = Field(default=256, ge=1, le=8192)
    temporary_budget_mb: int = Field(default=256, ge=1, le=8192)
    disk_reserve_mb: int = Field(default=512, ge=1, le=16384)
    snapshot_seconds: float = Field(default=60, ge=1, le=86400)
    snapshot_max_age: float = Field(default=86400, ge=1, le=604800)


class Configuration(StrictModel):
    format: int = Field(default=1, ge=1, le=1)
    version: int = Field(default=0, ge=0)
    devices: list[Device] = Field(default_factory=list, max_length=16)
    settings: Settings = Field(default_factory=Settings)

    @model_validator(mode="after")
    def validate_all(self, info: ValidationInfo):
        allow_legacy_overlap = bool(
            info.context and info.context.get("allow_legacy_endpoint_overlap")
        )
        ids, point_ids, endpoints, units = set(), set(), {}, {}
        for d in self.devices:
            if d.id in ids:
                raise ValueError("设备 ID 重复")
            ids.add(d.id)
            address = ipaddress.ip_address(d.host)
            for (host, port), name in endpoints.items():
                other = ipaddress.ip_address(host)
                if (
                    not allow_legacy_overlap
                    and port == d.port
                    and host != d.host
                    and address.version == other.version
                    and (
                        address.is_unspecified
                        or other.is_unspecified
                        or address == other
                    )
                ):
                    raise ValueError(
                        f"监听地址冲突：{d.host}:{d.port} 与“{name}”的 "
                        f"{host}:{port} 重叠。共享端口请使用相同监听 IP "
                        "和不同 Unit ID，或修改端口"
                    )
            endpoints.setdefault((d.host, d.port), d.name)
            key = (d.host, d.port, d.unit_id)
            if key in units:
                raise ValueError(
                    f"同一端点 Unit ID 重复：{d.host}:{d.port} 的 Unit ID "
                    f"{d.unit_id} 已由“{units[key]}”使用，请修改 Unit ID 或监听端点"
                )
            units[key] = d.name
            for p in d.points:
                if p.id in point_ids:
                    raise ValueError("全局点位 ID 必须唯一")
                point_ids.add(p.id)
        if len(endpoints) > 8 or len(point_ids) > 10000:
            raise ValueError("超过 8 个端点或 10000 个点位限制")
        if not set(self.settings.history_points).issubset(point_ids):
            raise ValueError("历史采样引用不存在的点位")
        estimated = (
            len(self.model_dump_json().encode()) * 5
            + len(point_ids) * 7000
            + len(self.devices) * 600000
        )
        if estimated > 128 * 1024 * 1024:
            raise ValueError("设备状态及配置估算超过 128MiB 内存预算")
        if self.settings.history_enabled:
            daily = (
                len(self.settings.history_points)
                * 86400
                / self.settings.history_interval
                * 512
            )
            if (
                daily * self.settings.retention_days
                > self.settings.telemetry_budget_mb * 1024 * 1024 * 0.55
            ):
                raise ValueError(
                    "历史容量估算超过预算：请减少采样点位、降低采样率或缩短保留期"
                )
        return self


def thermal_template(name="温控设备", host="127.0.0.1", port=1502, unit_id=1):
    command = Point(
        name="启动命令",
        area="coil",
        type="Bool",
        writable=True,
        write_mode="control",
        initial=False,
    )
    target = Point(
        name="目标温度",
        type="Float32",
        address=0,
        initial=60,
        unit="℃",
        writable=True,
        write_mode="control",
    )
    actual = Point(
        name="实际温度",
        type="Float32",
        area="input",
        initial=25,
        unit="℃",
        strategy=Strategy(
            kind="thermal",
            interval=0.1,
            dependencies=[command.id, target.id],
            params={"ambient": 25, "response_time": 10, "noise": 0},
        ),
    )
    alarm = Point(
        name="超温报警",
        area="discrete",
        type="Bool",
        initial=False,
        strategy=Strategy(
            kind="alarm",
            interval=0.1,
            dependencies=[actual.id],
            params={"threshold": 80, "hysteresis": 2},
        ),
    )
    return Device(
        name=name,
        host=host,
        port=port,
        unit_id=unit_id,
        points=[command, target, actual, alarm],
    )
