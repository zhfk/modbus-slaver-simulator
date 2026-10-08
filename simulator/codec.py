import math
import struct
from decimal import Decimal, ROUND_HALF_UP

FORMATS = {
    "Int16": "h",
    "UInt16": "H",
    "Int32": "i",
    "UInt32": "I",
    "Float32": "f",
    "Float64": "d",
}


def width(kind: str) -> int:
    return 1 if kind == "Bool" else struct.calcsize(FORMATS[kind]) // 2


def permute(data: bytes, byte_order: str, word_order: str) -> bytes:
    words = [data[i : i + 2] for i in range(0, len(data), 2)]
    if byte_order == "little":
        words = [w[::-1] for w in words]
    if word_order == "little":
        words.reverse()
    return b"".join(words)


def encode(point, value) -> list[int]:
    if point.type == "Bool":
        if value not in (True, False, 0, 1):
            raise ValueError("Bool 只能为开／关或 0／1")
        return [int(bool(value))]
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("数值必须有限")
    raw = (value - point.offset) / point.scale
    if not math.isfinite(raw):
        raise ValueError("换算值超出范围")
    if not point.type.startswith("Float"):
        raw = int(Decimal(str(raw)).to_integral_value(rounding=ROUND_HALF_UP))
    try:
        data = struct.pack(">" + FORMATS[point.type], raw)
    except (struct.error, OverflowError) as exc:
        raise ValueError("数值超出数据类型可表示范围") from exc
    if point.type.startswith("Float") and not math.isfinite(
        struct.unpack(">" + FORMATS[point.type], data)[0]
    ):
        raise ValueError("浮点数溢出")
    data = permute(data, point.byte_order, point.word_order)
    return list(struct.unpack(">" + "H" * (len(data) // 2), data))


def decode(point, words: list[int]):
    if point.type == "Bool":
        return bool(words[0])
    data = struct.pack(">" + "H" * len(words), *words)
    data = permute(data, point.byte_order, point.word_order)
    raw = struct.unpack(">" + FORMATS[point.type], data)[0]
    return raw * point.scale + point.offset


def safe_number(value):
    return value if isinstance(value, bool) or math.isfinite(value) else None
