import test from "node:test";
import assert from "node:assert/strict";
import { buildTrendChart, nearestTrendPoint } from "../src/trend.ts";

test("empty, nonfinite and isolated samples do not invent readings", () => {
  assert.equal(buildTrendChart([]).path, "");
  assert.equal(
    buildTrendChart([
      [1, null],
      [2, Infinity],
    ]).points.length,
    0,
  );
  const chart = buildTrendChart([[1, 0]]);
  assert.equal(chart.points[0].x, 350);
  assert.equal(chart.points[0].y, 67.5);
  assert.equal(chart.markers.length, 1);
  assert.equal(chart.ticks.length, 1);
  assert.equal(chart.count, 1);
});

test("Y axis adapts to constant, changing and extreme finite values", () => {
  for (const values of [
    [25, 25],
    [-10, -10],
    [1e-300, 1e-300],
    [0, 100],
    [-1e308, 1e308],
    [Number.MAX_VALUE, Number.MAX_VALUE],
    [Number.MIN_VALUE, Number.MIN_VALUE],
  ]) {
    const chart = buildTrendChart(values.map((v, i) => [i, v]));
    assert.equal(chart.points.length, 2);
    assert.ok(!/NaN|Infinity/.test(chart.path));
    assert.ok(
      chart.points.every(
        (p) => Number.isFinite(p.y) && p.y >= 20 && p.y <= 115,
      ),
    );
    assert.ok(
      chart.yTicks.every(
        (t) => Number.isFinite(t.position) && !/NaN|∞|Infinity/.test(t.label),
      ),
    );
    if (
      values[0] === values[1] &&
      Math.abs(values[0]) < 1e308 &&
      values[0] !== Number.MIN_VALUE
    )
      assert.ok(Math.abs(chart.points[0].y - 67.5) < 1e-6);
  }
  assert.notDeepEqual(
    buildTrendChart([
      [1, 0],
      [2, 10],
    ]).yTicks,
    buildTrendChart([
      [1, 100],
      [2, 1000],
    ]).yTicks,
  );
});

test("Boolean transitions use 0/1 axes and steps, null samples break lines", () => {
  const chart = buildTrendChart(
    [
      [1, false],
      [2, true],
      [3, null],
      [4, false],
    ],
    true,
  );
  assert.deepEqual(
    chart.yTicks.map((t) => t.label),
    ["开 1", "关 0"],
  );
  assert.match(chart.path, /L240.00,115.00 L240.00,20.00/);
  assert.equal(chart.path.match(/M/g).length, 2);
  assert.equal(chart.markers.length, 3);
});

test("tooltip selects a real nearest observation across gaps and edges", () => {
  const chart = buildTrendChart([
    [1, 5],
    [2, null],
    [3, 15],
  ]);
  assert.equal(nearestTrendPoint(chart.points, -100).value, 5);
  assert.equal(nearestTrendPoint(chart.points, 500).value, 15);
  assert.equal(nearestTrendPoint(chart.points, 999).time, 3);
  assert.equal(nearestTrendPoint([], 1), null);
  assert.equal(nearestTrendPoint(chart.points, NaN), null);
});

test("time ticks remain informative for short ranges and date boundaries", () => {
  const short = buildTrendChart([
    [100, 0],
    [101, 1],
  ]);
  assert.equal(short.ticks.length, 7);
  assert.equal(new Set(short.ticks.map((t) => t.label)).size, 7);
  const across = buildTrendChart([
    [0, 0],
    [86400, 1],
  ]);
  assert.ok(across.ticks.every((t) => t.date && t.full));
});
