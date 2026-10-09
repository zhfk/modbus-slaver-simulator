<script setup lang="ts">
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from "vue";
type Row = Record<string, any>;
const config = ref<Row>({ version: 0, devices: [], settings: {} });
const devices = ref<Row[]>([]),
  points = ref<Row[]>([]),
  total = ref(0),
  deviceId = ref("");
const tab = ref("monitor"),
  page = ref(1),
  size = ref(50),
  search = ref(""),
  area = ref(""),
  filterState = ref(""),
  sort = ref("config");
const selected = ref<string[]>([]),
  notice = ref(""),
  error = ref(""),
  busy = ref(false),
  loaded = ref(false),
  stale = ref(false),
  lastGood = ref(0);
const health = ref<Row>({ storage: {}, endpoints: [] }),
  diagnostics = ref<Row[]>([]),
  onlyErrors = ref(false),
  expandedLog = ref<Row | null>(null);
const showNav = ref(false),
  showTrend = ref(false),
  pauseChart = ref(false),
  trendData = ref<Row>({}),
  trendKeys = ref<string[]>([]);
const modal = ref(""),
  draft = ref<Row>({}),
  draftOriginal = ref(""),
  draftError = ref(""),
  paramText = ref("{}"),
  sampleText = ref("[]");
const rangeText = ref("{}");
const editingId = ref(""),
  assigned = ref<Row[]>([]),
  assignValue = ref(""),
  valuePreview = ref<Row | null>(null);
const importFile = ref<File | null>(null),
  importMode = ref("update"),
  importPreview = ref<Row | null>(null),
  importStep = ref(1);
const historyRows = ref<Row[]>([]),
  historyStart = ref(localTime(Date.now() - 3600000)),
  historyEnd = ref(localTime(Date.now())),
  historyPoint = ref(""),
  historyCursor = ref<number | null>(null);
const addTemplate = ref(true),
  newName = ref("温控设备"),
  newHost = ref("127.0.0.1"),
  newPort = ref(1502),
  newUnit = ref(1);
const deviceMemory = new Map<string, Row>();
let scrollLock: { overflow: string; paddingRight: string } | null = null;
function lockBackground(locked: boolean) {
  const body = document.body;
  if (locked && !scrollLock) {
    const gutter = window.innerWidth - document.documentElement.clientWidth;
    const padding = parseFloat(getComputedStyle(body).paddingRight) || 0;
    scrollLock = {
      overflow: body.style.overflow,
      paddingRight: body.style.paddingRight,
    };
    body.style.overflow = "hidden";
    if (gutter > 0) body.style.paddingRight = `${padding + gutter}px`;
  } else if (!locked && scrollLock) {
    body.style.overflow = scrollLock.overflow;
    body.style.paddingRight = scrollLock.paddingRight;
    scrollLock = null;
  }
}
watch(modal, (kind) => lockBackground(Boolean(kind)), { flush: "sync" });
let restoring = false;
const detailLive = computed(() =>
  points.value.find((p) => p.id === editingId.value),
);
let timer: number | undefined,
  socket: WebSocket | null = null,
  pollCount = 0,
  polling = false,
  sending = false,
  liveSequence = 0,
  sequence = 0,
  focusReturn: HTMLElement | null = null;
const device = computed(() =>
  devices.value.find((d) => d.id === deviceId.value),
);
const deviceConfig = computed(() =>
  config.value.devices.find((d: Row) => d.id === deviceId.value),
);
const statusNames: Row = {
  stopped: "已停止",
  running: "运行中",
  starting: "启动中",
  stopping: "停止中",
  fault: "启动故障",
  paused: "策略暂停",
  held: "手动保持",
  temporary: "临时覆盖",
  error: "策略错误",
  idle: "无周期策略",
};
const areaNames: Row = {
  coil: "Coil",
  discrete: "DI",
  holding: "HR",
  input: "IR",
};
const strategyNames: Row = {
  none: "无策略",
  fixed: "固定值",
  random: "均匀随机",
  sine: "正弦波",
  ramp: "斜坡",
  walk: "随机游走",
  noise: "带噪声信号",
  sequence: "状态序列",
  link: "点位联动",
  expression: "表达式",
  replay: "历史回放",
  thermal: "温控联动",
  alarm: "回差报警",
};
const modeNames: Row = {
  hold: "保持写入值",
  temporary: "临时覆盖",
  continue: "继续模拟",
  control: "控制输入",
};
const parameters: Row = {
  fixed: [["value", "固定值", 0]],
  random: [
    ["min", "最小值", 0],
    ["max", "最大值", 100],
  ],
  sine: [
    ["mean", "均值", 50],
    ["amplitude", "幅度", 10],
    ["period", "周期（秒）", 60],
    ["phase", "相位（弧度）", 0],
  ],
  ramp: [
    ["start", "起点", 0],
    ["end", "终点", 100],
    ["duration", "持续秒数", 60],
  ],
  walk: [
    ["min", "下界", 0],
    ["max", "上界", 100],
    ["step", "步长", 1],
  ],
  noise: [
    ["mean", "均值", 50],
    ["amplitude", "幅度", 10],
    ["period", "周期（秒）", 60],
    ["noise", "噪声幅度", 1],
  ],
  link: [
    ["gain", "增益", 1],
    ["offset", "偏移", 0],
    ["response_time", "响应时间（秒）", 1],
  ],
  thermal: [
    ["ambient", "环境温度", 25],
    ["response_time", "响应时间（秒）", 10],
    ["noise", "噪声幅度", 0],
  ],
  alarm: [
    ["threshold", "报警阈值", 80],
    ["hysteresis", "回差", 2],
  ],
};
const integerSettings: Row = {
  telemetry_budget_mb: "历史库预算（MiB）",
  config_budget_mb: "配置库预算（MiB）",
  backup_budget_mb: "备份预算（MiB）",
  temporary_budget_mb: "临时目录预算（MiB）",
  disk_reserve_mb: "磁盘保留空间（MiB）",
  snapshot_seconds: "快照间隔（秒）",
  snapshot_max_age: "快照最大年龄（秒）",
  retention_days: "历史保留天数",
  history_interval: "采样周期（秒）",
};
const modalTitle = computed(
  () =>
    (
      ({
        point: editingId.value ? "编辑点位" : "新增点位",
        device: "设备设置",
        new: "新建设备",
        assign: "修改当前值",
        import: "Excel 导入",
        storage: "存储与恢复设置",
        history: "历史数据",
      }) as Row
    )[modal.value] || "",
);
const visibleIds = computed(() => points.value.map((p) => p.id));
const allSelected = computed(
  () =>
    points.value.length > 0 &&
    points.value.every((p) => selected.value.includes(p.id)),
);
const pointDraftDirty = computed(
  () => draftSignature() !== draftOriginal.value,
);
function draftSignature() {
  return JSON.stringify({
    draft: draft.value,
    ...(modal.value === "point"
      ? { params: paramText.value, samples: sampleText.value }
      : modal.value === "device"
        ? { ranges: rangeText.value }
        : {}),
  });
}
function strategyFields(kind: string): string[] {
  const keys = (parameters[kind] || []).map(([key]: string[]) => key);
  if (kind === "expression") keys.push("expression");
  if (["ramp", "sequence", "replay"].includes(kind)) keys.push("loop");
  if (["sequence", "replay"].includes(kind)) keys.push("values");
  return keys;
}
function localTime(ms: number) {
  return new Date(ms - new Date(ms).getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
function clock(value: number) {
  return value
    ? new Date(value * 1000).toLocaleTimeString("zh-CN")
    : "暂无请求";
}
function formatValue(p: Row) {
  if (p.quality === "non_finite") return "非有限值";
  if (p.type === "Bool") return p.value ? "开" : "关";
  return p.value == null ? "—" : Number(p.value).toFixed(p.precision ?? 2);
}
function bytes(value: number) {
  return value ? `${(value / 1048576).toFixed(1)} MiB` : "0 MiB";
}
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}
async function request(path: string, options: RequestInit = {}) {
  const response = await fetch(path, {
    ...options,
    signal: AbortSignal.timeout(10000),
    headers:
      options.body instanceof FormData
        ? options.headers
        : { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ message: `请求失败 ${response.status}` }));
    throw new Error(
      body.message +
        (body.details ? "\n" + JSON.stringify(body.details, null, 2) : ""),
    );
  }
  return response.json();
}
async function action(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  draftError.value = "";
  try {
    await work();
  } catch (e) {
    const message = (e as Error).message;
    if (modal.value) draftError.value = message;
    else error.value = message;
  } finally {
    busy.value = false;
  }
}
async function refreshConfig() {
  config.value = await request("/api/config");
}
async function loadPoints() {
  if (!deviceId.value) return;
  const ticket = ++sequence;
  const key = deviceId.value;
  const params = new URLSearchParams({
    page: String(page.value),
    size: String(size.value),
    search: search.value,
    area: area.value,
    state: filterState.value,
    sort: sort.value,
  });
  const data = await request(`/api/devices/${key}/points?${params}`);
  if (ticket !== sequence || key !== deviceId.value) return;
  const lastPage = Math.max(1, Math.ceil(data.total / size.value));
  if (page.value > lastPage) {
    page.value = lastPage;
    return;
  }
  points.value = data.items;
  total.value = data.total;
  lastGood.value = Date.now();
  stale.value = false;
}
async function refreshDevices() {
  devices.value = await request("/api/devices");
  if (!deviceId.value && devices.value.length)
    deviceId.value = devices.value[0]!.id;
  if (deviceId.value && !devices.value.some((d) => d.id === deviceId.value))
    deviceId.value = devices.value[0]?.id || "";
}
function connect() {
  if (socket && socket.readyState < 2) return;
  socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`,
  );
  const currentSocket = socket;
  socket.onmessage = (e) => {
    if (socket !== currentSocket) return;
    sending = false;
    const message = JSON.parse(e.data);
    if (message.device?.id !== deviceId.value || liveSequence !== sequence)
      return;
    if (message.version < (device.value?.version || 0)) return;
    const updates = new Map(message.items.map((p: Row) => [p.id, p]));
    points.value = points.value.map((p) => (updates.get(p.id) as Row) || p);
    const i = devices.value.findIndex((d) => d.id === message.device.id);
    if (i >= 0) devices.value[i] = message.device;
    lastGood.value = Date.now();
    stale.value = false;
  };
  socket.onclose = () => {
    if (socket === currentSocket) sending = false;
  };
  socket.onerror = () => {
    stale.value = true;
  };
}
async function poll() {
  if (polling) return;
  polling = true;
  pollCount++;
  try {
    await refreshDevices();
    if (deviceId.value) {
      if (socket?.readyState === 1 && !sending) {
        sending = true;
        liveSequence = sequence;
        socket.send(
          JSON.stringify({ device: deviceId.value, ids: visibleIds.value }),
        );
      } else if (pollCount % 2 === 0) {
        await loadPoints();
      }
      if (tab.value === "diagnostics") {
        const key = deviceId.value;
        const errorsOnly = onlyErrors.value;
        const data = await request(
          `/api/diagnostics?device=${key}&errors_only=${errorsOnly}`,
        );
        if (key === deviceId.value && errorsOnly === onlyErrors.value)
          diagnostics.value = data.items;
      }
      if (showTrend.value && !pauseChart.value && trendKeys.value.length) {
        const key = deviceId.value;
        const ids = trendKeys.value.join(",");
        const data = await request(`/api/devices/${key}/trends?ids=${ids}`);
        if (key === deviceId.value && ids === trendKeys.value.join(","))
          trendData.value = data;
      }
    }
    if (pollCount % 5 === 0) health.value = await request("/api/health");
    if (pollCount % 5 === 0 && deviceId.value) await loadPoints();
    if (!socket || socket.readyState >= 2) connect();
    if (lastGood.value && Date.now() - lastGood.value > 4000)
      stale.value = true;
  } catch (e) {
    stale.value = true;
    error.value = (e as Error).message;
  } finally {
    polling = false;
  }
}
async function selectDevice(key: string) {
  if (!(await closeModal())) return;
  deviceId.value = key;
  showNav.value = false;
}
watch(deviceId, async (key, previous) => {
  if (deviceMemory.size >= 16)
    deviceMemory.delete(deviceMemory.keys().next().value!);
  if (previous)
    deviceMemory.set(previous, {
      page: page.value,
      size: size.value,
      search: search.value,
      area: area.value,
      state: filterState.value,
      sort: sort.value,
      scrollY: scrollY,
    });
  restoring = true;
  const remembered = deviceMemory.get(key);
  page.value = remembered?.page || 1;
  size.value = remembered?.size || 50;
  search.value = remembered?.search || "";
  area.value = remembered?.area || "";
  filterState.value = remembered?.state || "";
  sort.value = remembered?.sort || "config";
  selected.value = [];
  trendKeys.value = [];
  trendData.value = {};
  showTrend.value = false;
  points.value = [];
  historyPoint.value = "";
  await nextTick();
  restoring = false;
  if (key) {
    await loadPoints().catch((e) => (error.value = e.message));
    window.scrollTo(0, remembered?.scrollY || 0);
  }
});
watch([search, area, filterState, size, sort], () => {
  if (restoring) return;
  if (selected.value.length) notice.value = "筛选已变化，已清空当前选择";
  page.value = 1;
  selected.value = [];
  loadPoints().catch((e) => (error.value = e.message));
});
watch(page, () => {
  if (restoring) return;
  if (selected.value.length) notice.value = "已切换分页，已清空当前选择";
  selected.value = [];
  loadPoints().catch((e) => (error.value = e.message));
});
watch(onlyErrors, () => poll());
function toggleAll() {
  selected.value = allSelected.value ? [] : visibleIds.value.slice();
}
function toggleRow(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((k) => k !== id)
    : [...selected.value, id];
}
async function openModal(kind: string) {
  if (!(await closeModal())) return false;
  focusReturn = document.activeElement as HTMLElement;
  modal.value = kind;
  draftError.value = "";
  await nextTick();
  document
    .querySelector<HTMLElement>('[role="dialog"] input, [role="dialog"] button')
    ?.focus({ preventScroll: true });
  return true;
}
async function closeModal() {
  if (!modal.value) return true;
  if (
    modal.value &&
    ["point", "device", "storage"].includes(modal.value) &&
    pointDraftDirty.value &&
    !confirm("当前修改尚未保存，放弃这些修改？")
  )
    return false;
  modal.value = "";
  draftError.value = "";
  await nextTick();
  focusReturn?.focus({ preventScroll: true });
  return true;
}
function keydown(e: KeyboardEvent) {
  if (!modal.value) return;
  if (e.key === "Escape") {
    e.preventDefault();
    closeModal();
  }
  if (e.key === "Tab") {
    const items = [
      ...document.querySelectorAll<HTMLElement>(
        '[role="dialog"] button:not(:disabled), [role="dialog"] input:not(:disabled), [role="dialog"] select:not(:disabled), [role="dialog"] textarea:not(:disabled), [role="dialog"] [tabindex="0"]',
      ),
    ];
    const first = items[0],
      last = items.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  }
}
async function saveConfig(candidate: Row) {
  config.value = await request("/api/config", {
    method: "PUT",
    body: JSON.stringify(candidate),
  });
  await refreshDevices();
  await loadPoints();
  notice.value = `配置版本 ${config.value.version} 已保存并生效`;
}
async function deviceAction(kind: string) {
  if (!device.value) return;
  if (
    kind === "stop" &&
    !confirm(
      `停止 ${device.value.name} 后该设备不再响应 Modbus 请求，是否继续？`,
    )
  )
    return;
  if (
    kind === "reset" &&
    !confirm("将全部点位恢复初始值并清除保持状态，是否继续？")
  )
    return;
  await action(async () => {
    await request(`/api/devices/${deviceId.value}/actions/${kind}`, {
      method: "POST",
    });
    await refreshDevices();
    await loadPoints();
    notice.value =
      kind === "pause"
        ? "已暂停策略，Modbus 仍可读写"
        : kind === "resume"
          ? "已恢复设备策略，点位手动保持仍需单独恢复"
          : "设备操作已完成";
  });
}
function validateDeviceEndpoint(
  host: string,
  port: number,
  unit: number,
  id = "",
) {
  if (!Number.isInteger(unit) || unit < 0 || unit > 255)
    throw new Error("Unit ID 必须是 0～255 的整数");
  const overlap = config.value.devices.find((d: Row) => {
    if (d.id === id || d.port !== port || d.host === host) return false;
    const ipv6 = host.includes(":"),
      otherIpv6 = d.host.includes(":");
    const wildcard = (ip: string) => ip === "0.0.0.0" || ip === "::";
    return ipv6 === otherIpv6 && (wildcard(host) || wildcard(d.host));
  });
  if (overlap)
    throw new Error(
      `监听地址冲突：${host}:${port} 与“${overlap.name}”的 ${overlap.host}:${port} 重叠。共享端口请使用相同监听 IP 和不同 Unit ID，或修改端口。`,
    );
  const conflict = config.value.devices.find(
    (d: Row) =>
      d.id !== id && d.host === host && d.port === port && d.unit_id === unit,
  );
  if (conflict)
    throw new Error(
      `同一端点 Unit ID 重复：${host}:${port} 的 Unit ID ${unit} 已由“${conflict.name}”使用，请修改 Unit ID 或监听端点。`,
    );
}
async function createDevice() {
  await action(async () => {
    await refreshConfig();
    validateDeviceEndpoint(newHost.value, newPort.value, newUnit.value);
    if (addTemplate.value) {
      const result = await request("/api/templates/thermal", {
        method: "POST",
        body: JSON.stringify({
          version: config.value.version,
          name: newName.value,
          host: newHost.value,
          port: newPort.value,
          unit_id: newUnit.value,
        }),
      });
      await refreshConfig();
      await refreshDevices();
      deviceId.value = result.id;
    } else {
      const candidate = clone(config.value);
      const id = crypto.randomUUID();
      candidate.devices.push({
        id,
        name: newName.value,
        host: newHost.value,
        port: newPort.value,
        unit_id: newUnit.value,
        points: [],
      });
      await saveConfig(candidate);
      deviceId.value = id;
    }
    modal.value = "";
    notice.value = "设备已创建；点击“启动设备”后开始监听";
  });
}
async function editDevice() {
  if (!deviceConfig.value) return;
  if (!(await openModal("device"))) return;
  draft.value = clone(deviceConfig.value);
  rangeText.value = JSON.stringify(draft.value.valid_ranges || {}, null, 2);
  draftOriginal.value = draftSignature();
}
async function saveDevice() {
  await action(async () => {
    validateDeviceEndpoint(
      draft.value.host,
      draft.value.port,
      draft.value.unit_id,
      draft.value.id,
    );
    draft.value.valid_ranges = JSON.parse(rangeText.value);
    const candidate = clone(config.value);
    candidate.devices = candidate.devices.map((d: Row) =>
      d.id === deviceId.value ? draft.value : d,
    );
    await saveConfig(candidate);
    modal.value = "";
  });
}
async function removeDevice() {
  if (
    !confirm(
      `删除 ${device.value?.name} 及全部点位和依赖配置？必须先停止设备。`,
    )
  )
    return;
  await action(async () => {
    const candidate = clone(config.value);
    const ids = new Set(deviceConfig.value.points.map((p: Row) => p.id));
    candidate.devices = candidate.devices.filter(
      (d: Row) => d.id !== deviceId.value,
    );
    candidate.settings.history_points =
      candidate.settings.history_points.filter((id: string) => !ids.has(id));
    await saveConfig(candidate);
  });
}
function defaultPoint(): Row {
  return {
    id: crypto.randomUUID(),
    name: "新点位",
    description: "",
    group: "",
    area: "holding",
    address:
      Math.max(
        -1,
        ...(deviceConfig.value?.points || [])
          .filter((p: Row) => p.area === "holding")
          .map(
            (p: Row) =>
              p.address +
              (p.type.includes("32") ? 1 : p.type === "Float64" ? 3 : 0),
          ),
      ) + 1,
    type: "UInt16",
    byte_order: "big",
    word_order: "big",
    scale: 1,
    offset: 0,
    unit: "",
    precision: 2,
    initial: 0,
    writable: false,
    write_mode: "hold",
    override_seconds: 10,
    restore: "snapshot",
    strategy: {
      kind: "none",
      enabled: true,
      interval: 1,
      seed: 1,
      dependencies: [],
      params: {},
    },
  };
}
async function editPoint(row?: Row) {
  if (!(await openModal("point"))) return;
  editingId.value = row?.id || "";
  draft.value = clone(
    row
      ? deviceConfig.value.points.find((p: Row) => p.id === row.id)
      : defaultPoint(),
  );
  const fields = strategyFields(draft.value.strategy.kind);
  paramText.value = JSON.stringify(
    Object.fromEntries(
      Object.entries(draft.value.strategy.params).filter(
        ([key]) => !fields.includes(key),
      ),
    ),
    null,
    2,
  );
  sampleText.value = JSON.stringify(
    draft.value.strategy.params.values || [
      [0, 0],
      [10, 100],
    ],
    null,
    2,
  );
  draftOriginal.value = draftSignature();
}
function changeArea() {
  const bit = ["coil", "discrete"].includes(draft.value.area);
  draft.value.type = bit ? "Bool" : "UInt16";
  if (bit) {
    draft.value.scale = 1;
    draft.value.offset = 0;
    draft.value.initial = false;
  }
  if (["discrete", "input"].includes(draft.value.area))
    draft.value.writable = false;
}
function changeStrategy() {
  draft.value.strategy.params = {};
  for (const [key, , value] of parameters[draft.value.strategy.kind] || [])
    draft.value.strategy.params[key] = value;
  draft.value.strategy.dependencies = [];
  paramText.value = "{}";
  if (
    draft.value.strategy.kind !== "none" &&
    draft.value.write_mode === "control"
  )
    draft.value.write_mode = "hold";
}
async function savePoint() {
  await action(async () => {
    const point = clone(draft.value);
    if (["sequence", "replay"].includes(point.strategy.kind))
      point.strategy.params.values = JSON.parse(sampleText.value);
    const extra = JSON.parse(paramText.value);
    if (!extra || Array.isArray(extra) || typeof extra !== "object")
      throw new Error("附加策略参数必须是 JSON 对象");
    const fields = strategyFields(point.strategy.kind);
    const duplicated = Object.keys(extra).filter((key) => fields.includes(key));
    if (duplicated.length)
      throw new Error(`请在上方表单修改这些参数：${duplicated.join("、")}`);
    point.strategy.params = {
      ...extra,
      ...Object.fromEntries(
        Object.entries(point.strategy.params).filter(([key]) =>
          fields.includes(key),
        ),
      ),
    };
    const candidate = clone(config.value);
    const d = candidate.devices.find((d: Row) => d.id === deviceId.value);
    const index = d.points.findIndex((p: Row) => p.id === editingId.value);
    if (index >= 0) d.points[index] = point;
    else d.points.push(point);
    await saveConfig(candidate);
    modal.value = "";
  });
}
async function deletePoints(ids: string[]) {
  if (
    !confirm(
      `删除 ${ids.length} 个点位？设备须停止，被其他点位依赖的点位无法直接删除。`,
    )
  )
    return;
  await action(async () => {
    const candidate = clone(config.value);
    const d = candidate.devices.find((d: Row) => d.id === deviceId.value);
    d.points = d.points.filter((p: Row) => !ids.includes(p.id));
    candidate.settings.history_points =
      candidate.settings.history_points.filter(
        (id: string) => !ids.includes(id),
      );
    await saveConfig(candidate);
    selected.value = [];
  });
}
async function pointAction(kind: string, ids: string[]) {
  await action(async () => {
    await request(`/api/devices/${deviceId.value}/points/actions/${kind}`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    await loadPoints();
    notice.value =
      kind === "resume"
        ? "已解除点位保持；设备暂停时仍不会生成新值"
        : "已暂停所选点位策略";
  });
}
async function openAssign(rows: Row[]) {
  if (!(await openModal("assign"))) return;
  assigned.value = rows;
  assignValue.value =
    rows[0]?.type === "Bool"
      ? rows[0].value
        ? "1"
        : "0"
      : String(rows[0]?.value ?? 0);
  valuePreview.value = null;
}
function numericValue() {
  if (assigned.value.every((p) => p.type === "Bool"))
    return assignValue.value === "1";
  const value = Number(assignValue.value);
  if (!String(assignValue.value).trim() || !Number.isFinite(value))
    throw new Error("请输入有限数值");
  return value;
}
async function previewAssign() {
  await action(async () => {
    const previews = [];
    for (const p of assigned.value)
      previews.push(
        await request(`/api/devices/${deviceId.value}/preview-value`, {
          method: "POST",
          body: JSON.stringify({ id: p.id, value: numericValue() }),
        }),
      );
    valuePreview.value = { items: previews };
  });
}
async function applyAssign() {
  await action(async () => {
    await request(`/api/devices/${deviceId.value}/assign`, {
      method: "POST",
      body: JSON.stringify({
        items: assigned.value.map((p) => ({ id: p.id, value: numericValue() })),
      }),
    });
    await loadPoints();
    modal.value = "";
    notice.value = "当前值已修改；初始值保持原配置";
  });
}
async function setInitial(row: Row) {
  if (row.value == null) return;
  await action(async () => {
    const candidate = clone(config.value);
    const point = candidate.devices
      .find((d: Row) => d.id === deviceId.value)
      .points.find((p: Row) => p.id === row.id);
    point.initial = row.value;
    await saveConfig(candidate);
    notice.value = "已保存初始值；当前值未重置";
  });
}
async function openStorage() {
  if (!(await openModal("storage"))) return;
  draft.value = clone(config.value.settings);
  draftOriginal.value = draftSignature();
}
async function saveStorage() {
  await action(async () => {
    const candidate = clone(config.value);
    candidate.settings = draft.value;
    await saveConfig(candidate);
    modal.value = "";
  });
}
async function openImport() {
  if (!(await openModal("import"))) return;
  importFile.value = null;
  importPreview.value = null;
  importStep.value = 1;
}
async function previewImport() {
  if (!importFile.value) return;
  await action(async () => {
    const form = new FormData();
    form.append("file", importFile.value!);
    importPreview.value = await request(
      `/api/import/preview?mode=${importMode.value}&target=${deviceId.value}`,
      { method: "POST", body: form },
    );
    importStep.value = 2;
  });
}
async function applyImport() {
  await action(async () => {
    if (
      importMode.value === "replace" &&
      !confirm(
        `将替换当前设备配置并删除 ${importPreview.value?.deleted || 0} 个点位，是否继续？`,
      )
    )
      return;
    await request("/api/import/apply", {
      method: "POST",
      body: JSON.stringify(importPreview.value?.config),
    });
    await refreshConfig();
    await refreshDevices();
    await loadPoints();
    importStep.value = 3;
    notice.value = "配置导入成功；不会自动启动设备";
  });
}
function errorDownload() {
  const blob = new Blob(
    [JSON.stringify(importPreview.value?.errors || [], null, 2)],
    { type: "application/json" },
  );
  downloadBlob(blob, "modbus-import-errors.json");
}
function downloadBlob(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
async function exportData(kind: string) {
  await action(async () => {
    let query = `device=${deviceId.value}`;
    if (kind === "history" && historyPoint.value)
      query += `&ids=${historyPoint.value}`;
    else if (kind !== "history" && selected.value.length)
      query += `&ids=${selected.value.join(",")}`;
    if (kind === "history")
      query += `&start=${new Date(historyStart.value).getTime() / 1000}&end=${new Date(historyEnd.value).getTime() / 1000}`;
    const response = await fetch(`/api/export/${kind}?${query}`, {
      signal: AbortSignal.timeout(65000),
    });
    if (!response.ok) {
      const body = await response.json();
      throw new Error(body.message);
    }
    downloadBlob(await response.blob(), `modbus-${kind}.xlsx`);
    notice.value = "导出完成";
  });
}
async function viewTrend() {
  const keys = selected.value.length
    ? selected.value
    : points.value
        .filter((p) => p.type !== "Bool")
        .slice(0, 1)
        .map((p) => p.id);
  if (keys.length > 4) {
    error.value = "趋势最多选择 4 个点位，请减少选择";
    return;
  }
  trendKeys.value = keys;
  showTrend.value = true;
  await poll();
}
function chartPath(key: string) {
  const data = (trendData.value[key] as [number, number | null][]) || [];
  const valid = data.filter((row) => row[1] != null);
  if (!valid.length) return "";
  const min = Math.min(...valid.map((r) => r[1] as number)),
    max = Math.max(...valid.map((r) => r[1] as number));
  const first = data[0]![0],
    last = data.at(-1)![0];
  let open = false;
  return data
    .map(([t, v]) => {
      if (v == null) {
        open = false;
        return "";
      }
      const x = 20 + ((t - first) / Math.max(1, last - first)) * 660,
        y = 115 - ((v - min) / Math.max(0.001, max - min)) * 95;
      const part = `${open ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
      open = true;
      return part;
    })
    .join(" ");
}
function chartRange(key: string) {
  const values = (trendData.value[key] || [])
    .map((r: [number, number | null]) => r[1])
    .filter((v: number | null) => v != null);
  return values.length
    ? `${Math.min(...values).toFixed(2)} ～ ${Math.max(...values).toFixed(2)}`
    : "等待采样";
}
async function capturePackets() {
  await action(async () => {
    const stopping = health.value.capture?.remaining > 0;
    await request("/api/capture", {
      method: "POST",
      body: JSON.stringify({ seconds: stopping ? 0 : 60 }),
    });
    health.value = await request("/api/health");
    notice.value = stopping
      ? "报文捕获已停止"
      : "开始限时捕获：60 秒或 4MiB，先到即停止；记录受历史库保留策略限制";
  });
}
async function openHistory() {
  if (!(await openModal("history"))) return;
  historyPoint.value = historyPoint.value || points.value[0]?.id || "";
  historyRows.value = [];
  historyCursor.value = null;
}
async function queryHistory(more = false) {
  await action(async () => {
    if (!historyPoint.value) throw new Error("请选择点位");
    const query = new URLSearchParams({
      device: deviceId.value,
      point: historyPoint.value,
      start: String(new Date(historyStart.value).getTime() / 1000),
      end: String(new Date(historyEnd.value).getTime() / 1000),
      after: String(more ? historyCursor.value || 0 : 0),
    });
    const result = await request(`/api/history?${query}`);
    historyRows.value = more
      ? [...historyRows.value, ...result.items].slice(-2000)
      : result.items;
    historyCursor.value = result.next;
  });
}
function fileSelected(event: Event) {
  importFile.value = (event.target as HTMLInputElement).files?.[0] || null;
}
async function copyEndpoint() {
  if (!device.value) return;
  try {
    await navigator.clipboard.writeText(
      `${device.value.host}:${device.value.port}`,
    );
    notice.value = "监听地址已复制";
  } catch {
    notice.value = `监听地址：${device.value.host}:${device.value.port}`;
  }
}
onMounted(async () => {
  document.addEventListener("keydown", keydown);
  try {
    await refreshConfig();
    await refreshDevices();
    health.value = await request("/api/health");
    loaded.value = true;
    connect();
  } catch (e) {
    error.value = (e as Error).message;
  }
  timer = window.setInterval(poll, 1000);
});
onUnmounted(() => {
  lockBackground(false);
  clearInterval(timer);
  socket?.close();
  document.removeEventListener("keydown", keydown);
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: showNav }" :inert="Boolean(modal)">
      <div class="brand">
        <span class="brand-mark">M</span>
        <div><strong>Modbus Lab</strong><span>从机模拟器</span></div>
      </div>
      <div class="nav-heading">
        <span>设备工作区</span
        ><button aria-label="新建设备" @click="openModal('new')">＋</button>
      </div>
      <nav aria-label="设备列表">
        <button
          v-for="d in devices"
          :key="d.id"
          class="device-link"
          :class="{ active: d.id === deviceId }"
          @click="selectDevice(d.id)"
        >
          <strong>{{ d.name }}</strong
          ><span
            >{{ statusNames[d.status]
            }}<template v-if="d.paused"> · 策略暂停</template></span
          ><small>{{ d.host }}:{{ d.port }} / {{ d.unit_id }}</small>
        </button>
      </nav>
      <div class="sidebar-bottom">
        <button @click="openStorage">存储与恢复设置</button
        ><span>本机运行 · 后端提供页面</span>
      </div>
    </aside>
    <main :inert="Boolean(modal)">
      <header class="page-header">
        <div class="title-group">
          <button
            class="mobile-nav"
            aria-label="展开设备导航"
            @click="showNav = !showNav"
          >
            设备</button
          ><span class="eyebrow">MODBUS TCP / DEVICE WORKSPACE</span>
          <h1>{{ device?.name || "设备工作区" }}</h1>
          <button
            v-if="device"
            class="endpoint"
            @click="copyEndpoint"
            title="复制监听地址"
          >
            {{ device.host }}:{{ device.port }}
            <span>Unit {{ device.unit_id }}</span>
          </button>
        </div>
        <div v-if="device" class="header-actions">
          <button @click="editDevice">设备设置</button
          ><button
            v-if="device.status === 'running'"
            :disabled="busy"
            @click="deviceAction(device.paused ? 'resume' : 'pause')"
          >
            {{ device.paused ? "恢复策略" : "暂停策略" }}</button
          ><button
            class="primary"
            :disabled="busy || ['starting', 'stopping'].includes(device.status)"
            @click="
              deviceAction(device.status === 'running' ? 'stop' : 'start')
            "
          >
            {{ device.status === "running" ? "停止设备" : "启动设备" }}
          </button>
        </div>
      </header>
      <div v-if="error" class="message error" role="alert">
        <strong>操作未完成</strong>
        <pre>{{ error }}</pre>
        <button
          @click="
            action(async () => {
              await refreshConfig();
              await poll();
            })
          "
        >
          重新获取状态</button
        ><button @click="error = ''">关闭提示</button>
      </div>
      <div v-if="notice" class="message" role="status">
        {{ notice
        }}<button aria-label="关闭提示" @click="notice = ''">关闭</button>
      </div>
      <section v-if="!devices.length" class="empty panel">
        <span class="eyebrow">开始配置</span>
        <h2>{{ loaded ? "还没有设备" : "正在连接后端" }}</h2>
        <p>
          创建温控设备模板，或通过 Excel 导入设备与点位。配置完成后再启动 Modbus
          服务。
        </p>
        <div class="button-row">
          <button class="primary" @click="openModal('new')">新建设备</button
          ><button @click="openImport">从 Excel 导入</button
          ><button @click="exportData('template')">下载模板</button>
        </div>
      </section>
      <template v-if="device">
        <section class="status-strip" aria-label="运行状态">
          <div>
            <span>通信</span><strong>{{ statusNames[device.status] }}</strong>
          </div>
          <div>
            <span>策略</span
            ><strong>{{
              device.status !== "running"
                ? "未运行"
                : device.paused
                  ? "已暂停"
                  : "运行中"
            }}</strong>
          </div>
          <div>
            <span>端点连接</span><strong>{{ device.connections }}</strong>
          </div>
          <div>
            <span>最近请求</span
            ><strong>{{ clock(device.last_request) }}</strong>
          </div>
          <div>
            <span>点位</span><strong>{{ device.points }}</strong>
          </div>
          <div class="freshness">
            <strong>{{ stale ? "数据已过期" : "实时状态" }}</strong
            ><span>{{
              lastGood
                ? "更新于 " + new Date(lastGood).toLocaleTimeString("zh-CN")
                : "等待数据"
            }}</span>
          </div>
        </section>
        <div v-if="device.error" class="message error">{{ device.error }}</div>
        <nav class="tabs" aria-label="设备功能">
          <button
            :class="{ active: tab === 'monitor' }"
            @click="tab = 'monitor'"
          >
            点位监控</button
          ><button
            :class="{ active: tab === 'diagnostics' }"
            @click="
              tab = 'diagnostics';
              poll();
            "
          >
            通信诊断</button
          ><button
            :class="{ active: tab === 'settings' }"
            @click="tab = 'settings'"
          >
            设备设置
          </button>
        </nav>
        <section v-if="tab === 'monitor'" class="panel monitor-panel">
          <div class="toolbar">
            <div class="filters">
              <label class="search-field"
                ><span class="sr-only">搜索点位名称或协议地址</span
                ><input
                  v-model="search"
                  placeholder="搜索名称或协议地址" /></label
              ><select v-model="area" aria-label="筛选数据区">
                <option value="">全部数据区</option>
                <option
                  v-for="(label, key) in areaNames"
                  :key="key"
                  :value="key"
                >
                  {{ label }}
                </option></select
              ><select v-model="filterState" aria-label="筛选策略状态">
                <option value="">全部状态</option>
                <option
                  v-for="key in [
                    'running',
                    'held',
                    'temporary',
                    'paused',
                    'error',
                    'idle',
                    'stopped',
                  ]"
                  :key="key"
                  :value="key"
                >
                  {{ statusNames[key] }}
                </option>
              </select>
            </div>
            <div class="toolbar-actions">
              <button @click="editPoint()">新增点位</button
              ><button @click="openImport">导入</button>
              <details class="menu">
                <summary>导出 / 更多</summary>
                <div>
                  <strong>{{
                    selected.length
                      ? "已选 " + selected.length + " 个点位"
                      : "当前设备全部点位"
                  }}</strong
                  ><button @click="exportData('config')">
                    导出配置（含依赖）</button
                  ><button @click="exportData('snapshot')">导出当前快照</button
                  ><button @click="openHistory">历史数据</button
                  ><button @click="exportData('template')">
                    下载 Excel 模板</button
                  ><label
                    >排序<select v-model="sort">
                      <option value="config">配置顺序</option>
                      <option value="address">数据区与地址</option>
                    </select></label
                  >
                </div>
              </details>
            </div>
          </div>
          <div v-if="selected.length" class="batch-bar">
            <strong>已选 {{ selected.length }} 项（当前页）</strong
            ><button
              @click="openAssign(points.filter((p) => selected.includes(p.id)))"
            >
              批量赋值</button
            ><button @click="pointAction('pause', selected)">
              暂停所选策略</button
            ><button @click="pointAction('resume', selected)">
              恢复所选策略</button
            ><button @click="viewTrend">查看趋势</button
            ><button @click="deletePoints(selected)">删除</button
            ><button @click="selected = []">清空选择</button>
          </div>
          <div v-if="!points.length" class="empty compact">
            <h2>{{ device.points ? "没有匹配的点位" : "尚未配置点位" }}</h2>
            <p>
              {{
                device.points
                  ? "调整或清除筛选条件。"
                  : "添加点位，或下载模板填写后导入。"
              }}
            </p>
            <button
              v-if="device.points"
              @click="
                search = '';
                area = '';
                filterState = '';
              "
            >
              清除筛选</button
            ><button v-else @click="editPoint()">新增点位</button>
          </div>
          <div v-else class="table-scroll">
            <table class="points-table">
              <thead>
                <tr>
                  <th class="check">
                    <input
                      type="checkbox"
                      aria-label="选择当前页全部点位"
                      :checked="allSelected"
                      @change="toggleAll"
                    />
                  </th>
                  <th>名称</th>
                  <th class="mobile-secondary">协议地址</th>
                  <th class="optional">类型</th>
                  <th class="numeric">当前值 / 单位</th>
                  <th class="mobile-secondary">策略 / 状态</th>
                  <th class="optional">主机访问</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="p in points"
                  :key="p.id"
                  :class="{ selected: selected.includes(p.id) }"
                >
                  <td>
                    <input
                      type="checkbox"
                      :aria-label="'选择 ' + p.name"
                      :checked="selected.includes(p.id)"
                      @change="toggleRow(p.id)"
                    />
                  </td>
                  <td>
                    <button
                      class="text-button point-name"
                      @click="editPoint(p)"
                    >
                      {{ p.name }}</button
                    ><span v-if="p.group" class="cell-note">{{ p.group }}</span
                    ><span class="mobile-point-note"
                      >{{ areaNames[p.area] }} · {{ p.address }} /
                      {{ statusNames[p.state] }}</span
                    >
                  </td>
                  <td class="mono mobile-secondary">
                    {{ areaNames[p.area] }} · {{ p.address }}
                  </td>
                  <td class="optional">{{ p.type }}</td>
                  <td class="numeric live-value">
                    <strong>{{ formatValue(p) }}</strong
                    ><span>{{ p.unit }}</span>
                  </td>
                  <td class="mobile-secondary">
                    <span>{{ strategyNames[p.strategy.kind] }}</span
                    ><button
                      v-if="['held', 'temporary', 'error'].includes(p.state)"
                      class="state-button"
                      :title="p.error || '点击恢复策略'"
                      @click="pointAction('resume', [p.id])"
                    >
                      {{ statusNames[p.state]
                      }}<template v-if="p.state === 'temporary'">
                        {{ p.remaining.toFixed(0) }}s</template
                      >
                      · 恢复</button
                    ><span v-else class="cell-note">{{
                      statusNames[p.state]
                    }}</span>
                  </td>
                  <td class="optional">
                    {{ p.writable ? "主机可写" : "主机只读" }}
                  </td>
                  <td>
                    <button class="small" @click="openAssign([p])">赋值</button>
                    <details class="menu row-menu">
                      <summary aria-label="更多点位操作">更多</summary>
                      <div>
                        <button @click="editPoint(p)">查看 / 编辑</button
                        ><button
                          @click="setInitial(p)"
                          :disabled="p.value == null"
                        >
                          当前值设为初始值</button
                        ><button
                          @click="
                            selected = [p.id];
                            viewTrend();
                          "
                        >
                          查看趋势</button
                        ><button @click="pointAction('pause', [p.id])">
                          暂停策略</button
                        ><button @click="deletePoints([p.id])">删除点位</button>
                      </div>
                    </details>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <footer class="table-footer">
            <span>共 {{ total }} 个点位 · 地址从 0 开始</span
            ><button :disabled="page === 1" @click="page--">上一页</button
            ><span>{{ page }} / {{ Math.max(1, Math.ceil(total / size)) }}</span
            ><button :disabled="page * size >= total" @click="page++">
              下一页</button
            ><select v-model.number="size" aria-label="每页数量">
              <option :value="25">25 / 页</option>
              <option :value="50">50 / 页</option>
              <option :value="100">100 / 页</option></select
            ><button @click="viewTrend">实时趋势</button>
          </footer>
        </section>
        <section
          v-if="tab === 'monitor' && showTrend"
          class="panel trend-panel"
        >
          <div class="section-title">
            <div>
              <h2>实时趋势</h2>
              <p>最近 600 个采样 · 不同单位分图 · 实时缓存</p>
            </div>
            <div class="button-row">
              <button @click="pauseChart = !pauseChart">
                {{ pauseChart ? "恢复图表刷新" : "暂停图表刷新" }}</button
              ><button @click="showTrend = false">收起趋势</button>
            </div>
          </div>
          <div v-for="key in trendKeys" :key="key" class="chart">
            <div>
              <strong>{{
                deviceConfig.points.find((p: Row) => p.id === key)?.name
              }}</strong
              ><span
                >{{ chartRange(key) }}
                {{
                  deviceConfig.points.find((p: Row) => p.id === key)?.unit
                }}</span
              >
            </div>
            <svg
              viewBox="0 0 700 140"
              role="img"
              :aria-label="
                '点位 ' +
                deviceConfig.points.find((p: Row) => p.id === key)?.name +
                ' 的实时趋势'
              "
            >
              <path class="axis" d="M20 10V120H690" />
              <path class="signal" :d="chartPath(key)" />
            </svg>
            <div class="chart-axis">
              <span>{{
                trendData[key]?.length
                  ? clock(trendData[key][0][0])
                  : "等待采样"
              }}</span
              ><span>时间</span
              ><span>{{
                trendData[key]?.length ? clock(trendData[key].at(-1)[0]) : ""
              }}</span>
            </div>
          </div>
        </section>
        <section v-if="tab === 'diagnostics'" class="panel">
          <div class="section-title">
            <div>
              <h2>通信诊断</h2>
              <p>最近 100 条 · 原始报文保留在有界缓存中</p>
            </div>
            <label
              ><input type="checkbox" v-model="onlyErrors" />只看异常</label
            >
          </div>
          <div class="health-summary">
            <strong>运行健康</strong
            ><span
              >内存 {{ bytes(health.rss) }} · 调度延迟
              {{ Number(health.loop_delay_ms || 0).toFixed(2) }}ms · 线程
              {{ health.threads || 0 }}</span
            ><span
              >持久化队列 {{ health.storage.queue_count || 0 }} · 丢弃
              {{ health.storage.dropped || 0 }}</span
            ><button @click="openStorage">查看存储设置</button
            ><button :disabled="busy" @click="capturePackets">
              {{
                health.capture?.remaining > 0
                  ? "停止报文捕获"
                  : "临时捕获 60 秒"
              }}
            </button>
          </div>
          <div
            v-if="health.storage.error || health.storage.history_error"
            class="message error"
          >
            {{ health.storage.error || health.storage.history_error }}
          </div>
          <div class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>主机</th>
                  <th>Unit</th>
                  <th>功能码</th>
                  <th>地址 / 数量</th>
                  <th>耗时</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in diagnostics" :key="i">
                  <td>{{ clock(r.time) }}</td>
                  <td class="mono">{{ r.host }}</td>
                  <td>{{ r.unit_id }}</td>
                  <td>{{ r.function }}</td>
                  <td>{{ r.address }} / {{ r.quantity }}</td>
                  <td>{{ r.ms }}ms</td>
                  <td>
                    <button
                      class="text-button"
                      @click="expandedLog = expandedLog === r ? null : r"
                    >
                      {{ r.error || "正常" }} · 报文
                    </button>
                    <pre v-if="expandedLog === r" class="packet">
请求 {{ r.request }}
响应 {{ r.response || "无响应" }}</pre
                    >
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="!diagnostics.length" class="empty compact">
            <h3>暂无通信记录</h3>
            <p>外部主机连接当前监听端点后，将在这里显示真实请求。</p>
          </div>
        </section>
        <section v-if="tab === 'settings'" class="panel settings-summary">
          <h2>设备配置</h2>
          <dl>
            <dt>监听端点</dt>
            <dd>{{ device.host }}:{{ device.port }}</dd>
            <dt>Unit ID</dt>
            <dd>{{ device.unit_id }}</dd>
            <dt>功能码</dt>
            <dd>{{ deviceConfig.functions.join(", ") }}</dd>
            <dt>重启自动启动</dt>
            <dd>{{ deviceConfig.auto_start ? "已开启" : "关闭" }}</dd>
            <dt>未配置地址</dt>
            <dd>
              {{
                deviceConfig.missing_address === "zero"
                  ? "读取返回零；未映射写入拒绝"
                  : "无效地址异常"
              }}
            </dd>
          </dl>
          <div class="button-row">
            <button @click="editDevice">编辑设备配置</button
            ><button
              :disabled="device.status === 'running'"
              :title="device.status === 'running' ? '请先停止设备' : ''"
              @click="deviceAction('reset')"
            >
              重置全部当前值</button
            ><button @click="removeDevice">删除设备</button>
          </div>
        </section>
      </template>
    </main>
    <div v-if="modal" class="drawer-layer" @click.self="closeModal">
      <section
        class="drawer"
        :class="{ wide: ['import', 'history'].includes(modal) }"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
      >
        <header>
          <div>
            <span class="eyebrow">{{ device?.name || "工作区" }}</span>
            <h2 id="dialog-title">{{ modalTitle }}</h2>
          </div>
          <button aria-label="关闭抽屉" @click="closeModal">关闭</button>
        </header>
        <div class="drawer-content">
          <div v-if="draftError" class="message error" role="alert">
            <pre>{{ draftError }}</pre>
            <button
              type="button"
              @click="
                action(async () => {
                  await refreshConfig();
                  draftError =
                    '已获取配置版本，请比较修改后再次保存；原草稿已保留。';
                })
              "
            >
              刷新配置版本并保留草稿
            </button>
          </div>
          <form v-if="modal === 'new'" @submit.prevent="createDevice">
            <label
              >设备名称<input
                v-model="newName"
                required
                maxlength="128" /></label
            ><label
              >初始配置<select aria-label="初始配置" v-model="addTemplate">
                <option :value="true">温控模板（4 个联动点位）</option>
                <option :value="false">空设备</option>
              </select></label
            >
            <div class="form-grid">
              <label>监听 IP<input v-model="newHost" required /></label
              ><label
                >Modbus 端口<input
                  type="number"
                  v-model.number="newPort"
                  min="1"
                  max="65535"
                  required /></label
              ><label
                >Unit ID<input
                  type="number"
                  v-model.number="newUnit"
                  min="0"
                  max="255"
                  required
              /></label>
            </div>
            <p>
              Unit ID 为 0～255 的整数；同一监听 IP
              和端口下不能重复，不同端点可重复。 0.0.0.0 覆盖所有 IPv4
              地址，不能与具体 IPv4 地址另建同端口监听；IPv6 的 :: 同理。
            </p>
            <p>
              本机测试用 127.0.0.1；允许外部主机连接可绑定 0.0.0.0。Web
              管理端口仍仅供本机访问。
            </p>
            <button class="primary" :disabled="busy">创建设备</button>
          </form>
          <form
            v-if="modal === 'point' && draft.strategy"
            @submit.prevent="savePoint"
          >
            <p class="form-note">
              {{
                device?.status === "running"
                  ? "设备运行中：名称、初始值和策略可保存；映射、编码及增删点位须先停止设备。"
                  : "配置保存后生效，初始值只在初始化或重置时应用。"
              }}
            </p>
            <div v-if="detailLive" class="live-detail">
              <h3>运行时详情</h3>
              <strong
                >{{ formatValue(detailLive) }} {{ detailLive.unit }}</strong
              >
              <p>
                原始值 {{ detailLive.raw.join(", ") }} ·
                {{ statusNames[detailLive.state] }} · 更新来源
                {{ detailLive.source }} · {{ clock(detailLive.changed) }}
              </p>
              <p v-if="detailLive.error">策略错误：{{ detailLive.error }}</p>
              <p v-if="detailLive.state === 'held'">
                该点位保持人工或主机写入值，设备恢复策略不会自动解除。
              </p>
              <button
                v-if="['held', 'temporary', 'error'].includes(detailLive.state)"
                type="button"
                @click="pointAction('resume', [detailLive.id])"
              >
                恢复点位策略
              </button>
            </div>
            <h3>基本信息</h3>
            <label
              >名称<input v-model="draft.name" required maxlength="128"
            /></label>
            <div class="form-grid">
              <label>分组<input v-model="draft.group" /></label
              ><label
                >协议地址（从 0 开始）<input
                  type="number"
                  v-model.number="draft.address"
                  min="0"
                  max="65535"
                  required /></label
              ><label
                >数据区<select
                  aria-label="数据区"
                  v-model="draft.area"
                  @change="changeArea"
                >
                  <option
                    v-for="(label, key) in areaNames"
                    :key="key"
                    :value="key"
                  >
                    {{ label }}
                  </option>
                </select></label
              ><label
                >类型<select aria-label="类型" v-model="draft.type">
                  <option
                    v-for="kind in ['coil', 'discrete'].includes(draft.area)
                      ? ['Bool']
                      : [
                          'Int16',
                          'UInt16',
                          'Int32',
                          'UInt32',
                          'Float32',
                          'Float64',
                        ]"
                    :key="kind"
                  >
                    {{ kind }}
                  </option>
                </select></label
              >
            </div>
            <label
              >说明<textarea v-model="draft.description" rows="2"></textarea>
            </label>
            <h3>数值与权限</h3>
            <div class="form-grid">
              <label v-if="draft.type === 'Bool'"
                >初始值<select aria-label="初始值" v-model="draft.initial">
                  <option :value="false">关</option>
                  <option :value="true">开</option>
                </select></label
              ><label v-else
                >初始值（工程值）<input
                  type="number"
                  step="any"
                  v-model.number="draft.initial"
                  required /></label
              ><label>单位<input v-model="draft.unit" maxlength="32" /></label
              ><label v-if="draft.type !== 'Bool'"
                >倍率<input
                  type="number"
                  step="any"
                  v-model.number="draft.scale"
                  required /></label
              ><label v-if="draft.type !== 'Bool'"
                >偏移<input
                  type="number"
                  step="any"
                  v-model.number="draft.offset"
                  required /></label
              ><label
                >显示小数位<input
                  type="number"
                  v-model.number="draft.precision"
                  min="0"
                  max="10" /></label
              ><label
                >主机访问<select
                  aria-label="主机访问"
                  v-model="draft.writable"
                  :disabled="['discrete', 'input'].includes(draft.area)"
                >
                  <option :value="false">主机只读</option>
                  <option :value="true">主机可写</option>
                </select></label
              ><label
                >写入后行为<select
                  aria-label="写入后行为"
                  v-model="draft.write_mode"
                  @change="
                    draft.write_mode === 'control' &&
                    (draft.strategy = {
                      kind: 'none',
                      enabled: true,
                      interval: 1,
                      seed: 1,
                      params: {},
                      dependencies: [],
                    })
                  "
                >
                  <option
                    v-for="(label, key) in modeNames"
                    :key="key"
                    :value="key"
                  >
                    {{ label }}
                  </option>
                </select></label
              ><label v-if="draft.write_mode === 'temporary'"
                >覆盖持续秒数<input
                  type="number"
                  step="any"
                  v-model.number="draft.override_seconds"
                  min="0.01" /></label
              ><label
                >进程重启后<select
                  aria-label="进程重启后"
                  v-model="draft.restore"
                >
                  <option value="snapshot">恢复兼容快照</option>
                  <option value="initial">恢复初始值</option>
                </select></label
              >
            </div>
            <details>
              <summary>高级编码设置</summary>
              <div class="form-grid">
                <label
                  >字节顺序<select
                    aria-label="字节顺序"
                    v-model="draft.byte_order"
                  >
                    <option value="big">大端</option>
                    <option value="little">小端</option>
                  </select></label
                ><label
                  >寄存器顺序<select
                    aria-label="寄存器顺序"
                    v-model="draft.word_order"
                  >
                    <option value="big">高字在前</option>
                    <option value="little">低字在前</option>
                  </select></label
                >
              </div>
            </details>
            <h3>模拟策略</h3>
            <div class="form-grid">
              <label
                >策略类型<select
                  aria-label="策略类型"
                  v-model="draft.strategy.kind"
                  :disabled="draft.write_mode === 'control'"
                  @change="changeStrategy"
                >
                  <option
                    v-for="(label, key) in strategyNames"
                    :key="key"
                    :value="key"
                  >
                    {{ label }}
                  </option>
                </select></label
              ><label
                >更新周期（秒）<input
                  type="number"
                  step="any"
                  min="0.01"
                  v-model.number="draft.strategy.interval" /></label
              ><label
                >随机种子<input
                  type="number"
                  min="0"
                  v-model.number="draft.strategy.seed" /></label
              ><label
                ><input
                  type="checkbox"
                  v-model="draft.strategy.enabled"
                />启用策略</label
              ><label
                v-for="[key, label] in parameters[draft.strategy.kind] || []"
                :key="key"
                >{{ label
                }}<input
                  type="number"
                  step="any"
                  v-model.number="draft.strategy.params[key]"
              /></label>
            </div>
            <label
              v-if="
                ['link', 'expression', 'thermal', 'alarm'].includes(
                  draft.strategy.kind,
                )
              "
              >依赖点位（温控：先启动命令，再目标温度）<select
                aria-label="依赖点位"
                v-model="draft.strategy.dependencies"
                multiple
                size="4"
              >
                <option
                  v-for="p in deviceConfig.points.filter(
                    (p: Row) => p.id !== draft.id,
                  )"
                  :key="p.id"
                  :value="p.id"
                >
                  {{ p.name }} · {{ areaNames[p.area] }} {{ p.address }}
                </option>
              </select></label
            ><label v-if="draft.strategy.kind === 'expression'"
              >表达式（t、x0、x1 与四则运算）<input
                v-model="draft.strategy.params.expression"
                placeholder="x0 * 0.5 + t" /></label
            ><label v-if="['sequence', 'replay'].includes(draft.strategy.kind)"
              >样本（序列：持续秒数；回放：时间秒数）<textarea
                v-model="sampleText"
                rows="5"
              ></textarea></label
            ><label
              v-if="
                ['ramp', 'sequence', 'replay'].includes(draft.strategy.kind)
              "
              ><input
                type="checkbox"
                v-model="draft.strategy.params.loop"
              />循环</label
            >
            <details>
              <summary>附加策略参数（JSON）</summary>
              <p class="hint">
                上方已有的参数请在对应输入框修改；这里仅填写额外参数。
              </p>
              <textarea
                v-model="paramText"
                rows="4"
                aria-label="附加策略参数"
              ></textarea>
            </details>
            <div class="form-actions">
              <button class="primary" :disabled="busy">保存配置</button
              ><button type="button" @click="closeModal">取消</button>
            </div>
          </form>
          <form
            v-if="modal === 'device' && draft.faults"
            @submit.prevent="saveDevice"
          >
            <label>设备名称<input v-model="draft.name" required /></label
            ><label
              >说明<textarea v-model="draft.description"></textarea>
            </label>
            <h3>监听与协议</h3>
            <p>
              改变监听参数须先停止设备。Unit ID 为 0～255 的整数；同一监听 IP
              和端口下不能重复，不同端点可重复。 0.0.0.0 覆盖所有 IPv4
              地址，不能与具体 IPv4 地址另建同端口监听；IPv6 的 :: 同理。
            </p>
            <div class="form-grid">
              <label>绑定 IP<input v-model="draft.host" required /></label
              ><label
                >端口<input
                  type="number"
                  v-model.number="draft.port"
                  min="1"
                  max="65535" /></label
              ><label
                >Unit ID<input
                  type="number"
                  v-model.number="draft.unit_id"
                  min="0"
                  max="255"
                  required /></label
              ><label
                >最大连接数<input
                  type="number"
                  v-model.number="draft.max_connections"
                  min="1"
                  max="32" /></label
              ><label
                >空闲超时（秒）<input
                  type="number"
                  v-model.number="draft.idle_seconds" /></label
              ><label
                >组帧超时（秒）<input
                  type="number"
                  step="any"
                  v-model.number="draft.frame_seconds" /></label
              ><label
                >未配置地址<select
                  aria-label="未配置地址"
                  v-model="draft.missing_address"
                >
                  <option value="exception">无效地址异常</option>
                  <option value="zero">读取零值（未映射写入拒绝）</option>
                </select></label
              ><label
                >未知 Unit ID<select
                  aria-label="未知 Unit ID"
                  v-model="draft.unknown_unit"
                >
                  <option value="silence">不响应</option>
                  <option value="exception">网关目标无响应异常</option>
                </select></label
              >
            </div>
            <label
              >有效地址范围（JSON，各数据区为 [起始, 结束] 列表）<textarea
                v-model="rangeText"
                rows="3"
                placeholder='{"holding":[[0,100]]}'
              ></textarea>
            </label>
            <fieldset>
              <legend>启用功能码</legend>
              <label
                v-for="f in [1, 2, 3, 4, 5, 6, 15, 16, 22, 23]"
                :key="f"
                class="inline"
                ><input
                  type="checkbox"
                  :value="f"
                  v-model="draft.functions"
                />{{ f }}</label
              >
            </fieldset>
            <label
              ><input
                type="checkbox"
                v-model="draft.auto_start"
              />进程重启后自动启动此设备</label
            >
            <h3>设备身份</h3>
            <label
              ><input
                type="checkbox"
                v-model="draft.read_identity"
              />启用设备身份读取（43 / 14）</label
            >
            <div class="form-grid">
              <label
                >厂商<input
                  v-model="draft.identity.vendor"
                  maxlength="64" /></label
              ><label
                >产品<input
                  v-model="draft.identity.product"
                  maxlength="64" /></label
              ><label
                >版本<input v-model="draft.identity.revision" maxlength="64"
              /></label>
            </div>
            <h3>故障注入（默认关闭）</h3>
            <div class="form-grid">
              <label
                >响应延迟（毫秒）<input
                  type="number"
                  v-model.number="draft.faults.delay_ms"
                  min="0"
                  max="5000" /></label
              ><label
                >不响应概率（0～1）<input
                  type="number"
                  step="0.01"
                  v-model.number="draft.faults.timeout_rate"
                  min="0"
                  max="1" /></label
              ><label
                >断连概率（0～1）<input
                  type="number"
                  step="0.01"
                  v-model.number="draft.faults.disconnect_rate"
                  min="0"
                  max="1" /></label
              ><label
                ><input
                  type="checkbox"
                  v-model="draft.faults.freeze"
                />冻结策略生成</label
              >
            </div>
            <button class="primary" :disabled="busy">保存设备配置</button>
          </form>
          <form v-if="modal === 'assign'" @submit.prevent="applyAssign">
            <p>
              对
              {{ assigned.length }} 个点位赋值；批量操作全部成功或全部不执行。
            </p>
            <div class="assign-list" v-for="p in assigned" :key="p.id">
              <strong>{{ p.name }}</strong
              ><span
                >当前 {{ formatValue(p) }} {{ p.unit }} ·
                {{ modeNames[p.write_mode] }}</span
              >
            </div>
            <label
              >新工程值<select
                v-if="assigned.every((p) => p.type === 'Bool')"
                v-model="assignValue"
              >
                <option value="0">关</option>
                <option value="1">开</option></select
              ><input
                v-else
                v-model="assignValue"
                type="number"
                step="any"
                required
                @input="valuePreview = null"
            /></label>
            <p>
              保持写入会暂停所选点位的生成策略；继续模拟可能在下一周期覆盖本次赋值。
            </p>
            <button type="button" @click="previewAssign" :disabled="busy">
              校验并预览编码值
            </button>
            <ul v-if="valuePreview">
              <li v-for="(p, i) in valuePreview.items" :key="i">
                {{ assigned[i]?.name }}：实际值 {{ p.value }}，原始寄存器
                {{ p.raw.join(", ") }}
              </li>
            </ul>
            <div class="form-actions">
              <button class="primary" :disabled="busy">应用当前值</button
              ><button type="button" @click="closeModal">取消</button>
            </div>
          </form>
          <div v-if="modal === 'import'">
            <ol class="steps">
              <li :class="{ active: importStep === 1 }">选择文件</li>
              <li :class="{ active: importStep === 2 }">校验预览</li>
              <li :class="{ active: importStep === 3 }">应用结果</li>
            </ol>
            <template v-if="importStep === 1"
              ><label
                >配置文件（xlsx，最大 20MiB）<input
                  type="file"
                  accept=".xlsx"
                  @change="fileSelected" /></label
              ><label
                >导入方式<select aria-label="导入方式" v-model="importMode">
                  <option value="update">按稳定 ID 更新</option>
                  <option value="new">新增设备与点位</option>
                  <option value="replace" :disabled="!deviceId">
                    替换当前设备全部配置
                  </option>
                </select></label
              >
              <p>
                当前设备：{{ device?.name || "无" }}。配置模板使用从 0
                开始的协议地址；快照与历史文件不能作为配置导入。
              </p>
              <button @click="exportData('template')">下载模板</button
              ><button
                class="primary"
                :disabled="busy || !importFile"
                @click="previewImport"
              >
                解析并校验
              </button></template
            ><template v-if="importStep === 2 && importPreview"
              ><div v-if="importPreview.errors.length" class="message error">
                <strong
                  >发现
                  {{ importPreview.errors.length }} 个错误，配置尚未应用</strong
                >
                <ul>
                  <li v-for="(e, i) in importPreview.errors" :key="i">
                    {{ e.sheet }} · 第 {{ e.row }} 行 · {{ e.field }}：{{
                      e.message
                    }}
                  </li>
                </ul>
                <button @click="errorDownload">下载错误清单</button>
              </div>
              <template v-else
                ><div class="preview-counts">
                  <span>新增 {{ importPreview.added }}</span
                  ><span>更新 {{ importPreview.changed }}</span
                  ><span>删除 {{ importPreview.deleted }}</span
                  ><span>配置版本 {{ importPreview.version }}</span>
                </div>
                <div
                  v-if="importPreview.needs_stop.length"
                  class="message error"
                >
                  {{
                    importPreview.needs_stop.join("\n")
                  }}。关闭面板停止设备后，再次校验文件。
                </div>
                <p>
                  应用时会重新检查设备状态、引用关系和配置版本，不会自动停机或启动设备。
                </p></template
              >
              <div class="form-actions">
                <button
                  class="primary"
                  :disabled="
                    busy ||
                    importPreview.errors.length > 0 ||
                    importPreview.needs_stop.length > 0
                  "
                  @click="applyImport"
                >
                  应用配置</button
                ><button @click="importStep = 1">重新选择文件</button>
              </div></template
            >
            <div v-if="importStep === 3" class="empty compact">
              <h3>导入已完成</h3>
              <p>点位表格已刷新。需要运行时请明确启动设备。</p>
              <button @click="closeModal">返回点位监控</button>
            </div>
          </div>
          <form v-if="modal === 'storage'" @submit.prevent="saveStorage">
            <label
              >当前数据目录<textarea
                :value="health.storage.data_dir || ''"
                readonly
                rows="2"
                placeholder="正在获取数据目录…"
                aria-describedby="storage-directory-help"
              />
            </label>
            <p id="storage-directory-help">
              配置、历史、快照、备份和日志保存在此目录，可选中复制路径。更改目录请停止服务后使用
              <code>--data-dir</code> 重新启动。
            </p>
            <div class="health-summary">
              <span>磁盘余量 {{ bytes(health.storage.disk_free) }}</span
              ><span>最近快照 {{ clock(health.storage.last_snapshot) }}</span
              ><span>最近备份 {{ clock(health.storage.last_backup) }}</span>
            </div>
            <p>
              配置、实时值、历史采样分开管理。历史默认关闭，日志自动轮换；快照不保证每次写入在断电后都保留。
            </p>
            <label
              ><input
                type="checkbox"
                v-model="draft.history_enabled"
              />开启历史采样</label
            ><label
              >历史采样点位<select
                multiple
                size="6"
                v-model="draft.history_points"
              >
                <optgroup
                  v-for="d in config.devices"
                  :key="d.id"
                  :label="d.name"
                >
                  <option v-for="p in d.points" :key="p.id" :value="p.id">
                    {{ p.name }}
                  </option>
                </optgroup>
              </select></label
            >
            <div class="form-grid">
              <label v-for="(label, key) in integerSettings" :key="key"
                >{{ label
                }}<input
                  type="number"
                  step="any"
                  min="0.1"
                  v-model.number="draft[key]"
              /></label>
            </div>
            <p>
              磁盘不足时停止历史与捕获写入，保留内存中的 Modbus
              服务；配置保存会明确拒绝。数据库空间回收与备份恢复在停机维护时执行。
            </p>
            <div class="storage-files">
              <div v-for="(value, name) in health.storage.files" :key="name">
                <span>{{ name }}</span
                ><strong>{{ bytes(Number(value)) }}</strong>
              </div>
            </div>
            <button class="primary" :disabled="busy">保存存储设置</button>
          </form>
          <div v-if="modal === 'history'">
            <p>
              {{
                config.settings.history_enabled
                  ? "历史采样已启用；只保存选定点位。"
                  : "历史采样未启用；可查询已有记录，开启采样请到存储设置。"
              }}
            </p>
            <label
              >点位<select aria-label="历史点位" v-model="historyPoint">
                <option
                  v-for="p in deviceConfig.points"
                  :key="p.id"
                  :value="p.id"
                >
                  {{ p.name }}
                </option>
              </select></label
            >
            <div class="form-grid">
              <label
                >开始时间<input
                  type="datetime-local"
                  v-model="historyStart" /></label
              ><label
                >结束时间<input type="datetime-local" v-model="historyEnd"
              /></label>
            </div>
            <div class="button-row">
              <button class="primary" :disabled="busy" @click="queryHistory()">
                查询记录</button
              ><button :disabled="busy" @click="exportData('history')">
                导出历史（最多 10000 条）
              </button>
            </div>
            <div class="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>工程值</th>
                    <th>单位</th>
                    <th>原始值</th>
                    <th>配置版本</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in historyRows" :key="r.id">
                    <td>
                      {{ new Date(r.time * 1000).toLocaleString("zh-CN") }}
                    </td>
                    <td>{{ r.value }}</td>
                    <td>{{ r.metadata.unit }}</td>
                    <td>{{ r.raw.join(", ") }}</td>
                    <td>{{ r.version }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="!historyRows.length">当前时间范围暂无记录。</p>
            <button
              v-if="historyCursor"
              @click="queryHistory(true)"
              :disabled="busy"
            >
              加载下一页（界面最多保留 2000 条）
            </button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
