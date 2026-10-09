export type TrendSample = [number, number | boolean | null];
export type ChartPoint = { time: number; value: number; x: number; y: number };

function axisNumber(value: number) {
  const magnitude = Math.abs(value);
  return magnitude >= 1e6 || (magnitude > 0 && magnitude < 0.01)
    ? value.toExponential(1)
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(
        value,
      );
}

export function buildTrendChart(samples: TrendSample[], boolean = false) {
  const data = samples.filter(([time]) => Number.isFinite(time));
  const finite = data
    .map(([, value]) => (value == null ? null : Number(value)))
    .filter(
      (value): value is number => value != null && Number.isFinite(value),
    );
  const first = data[0]?.[0] ?? 0,
    last = data.at(-1)?.[0] ?? first;
  const span = Math.max(0, last - first);
  const count = span > 0 ? 7 : data.length ? 1 : 0;
  const crossDate =
    new Date(first * 1000).toDateString() !==
    new Date(last * 1000).toDateString();
  const ticks = Array.from({ length: count }, (_, index) => {
    const date = new Date(
      (first + (span * index) / Math.max(1, count - 1)) * 1000,
    );
    let label = date.toLocaleTimeString("zh-CN", {
      hourCycle: "h23",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    if (span > 0 && span < 6)
      label += `.${Math.floor(date.getMilliseconds() / 100)}`;
    return {
      index,
      label,
      date: crossDate ? `${date.getMonth() + 1}/${date.getDate()}` : "",
      full: date.toLocaleString(),
    };
  });
  const empty = {
    path: "",
    markers: [] as ChartPoint[],
    points: [] as ChartPoint[],
    yTicks: [] as { label: string; position: number }[],
    ticks,
    count: data.length,
    range: data.length ? "暂无有效数值" : "等待采样",
  };
  if (!finite.length) return empty;
  const minimum = Math.min(...finite),
    maximum = Math.max(...finite);
  const magnitude = Math.max(Math.abs(minimum), Math.abs(maximum), 1);
  let lower = minimum / magnitude,
    upper = maximum / magnitude;
  if (boolean) {
    lower = 0;
    upper = 1;
  } else {
    const padding =
      upper === lower
        ? lower === 0
          ? 1 / magnitude
          : Math.max(Math.abs(lower) * 0.05, Number.MIN_VALUE)
        : (upper - lower) * 0.1;
    const limit = Number.MAX_VALUE / magnitude;
    lower = Math.max(-limit, lower - padding);
    upper = Math.min(limit, upper + padding);
  }
  function y(value: number) {
    return (
      115 -
      (((boolean ? value : value / magnitude) - lower) / (upper - lower)) * 95
    );
  }
  const points: (ChartPoint | null)[] = data.map(([time, raw]) => {
    const value = raw == null ? null : Number(raw);
    return value == null || !Number.isFinite(value)
      ? null
      : {
          time,
          value,
          x: span > 0 ? 20 + ((time - first) / span) * 660 : 350,
          y: y(value),
        };
  });
  let previous: ChartPoint | null = null;
  const path = points
    .map((point) => {
      if (!point) {
        previous = null;
        return "";
      }
      const step =
        boolean && previous
          ? `L${point.x.toFixed(2)},${previous.y.toFixed(2)} `
          : "";
      const command = previous ? "L" : "M";
      previous = point;
      return `${step}${command}${point.x.toFixed(2)},${point.y.toFixed(2)}`;
    })
    .join(" ");
  const markers = points.filter((point, i): point is ChartPoint =>
    Boolean(point && (!points[i - 1] || !points[i + 1])),
  );
  const values = boolean
    ? [1, 0]
    : [
        upper * magnitude,
        (lower / 2 + upper / 2) * magnitude,
        lower * magnitude,
      ];
  return {
    ...empty,
    path,
    markers,
    points: points.filter((point): point is ChartPoint => point !== null),
    range: `${axisNumber(minimum)} ～ ${axisNumber(maximum)}`,
    yTicks: values.map((value) => ({
      label: boolean ? (value ? "开 1" : "关 0") : axisNumber(value),
      position: (y(value) / 140) * 100,
    })),
  };
}

export function nearestTrendPoint(points: ChartPoint[], x: number) {
  if (!points.length || !Number.isFinite(x)) return null;
  return points.reduce((nearest, point) =>
    Math.abs(point.x - x) < Math.abs(nearest.x - x) ? point : nearest,
  );
}
