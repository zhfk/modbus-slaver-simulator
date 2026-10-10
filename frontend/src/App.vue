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
import FeedbackBubbles, { type Feedback } from "./FeedbackBubbles.vue";
import HelpPage from "./HelpPage.vue";
import TourGuide, { guideSteps } from "./TourGuide.vue";
import { buildTrendChart, nearestTrendPoint } from "./trend";
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
const helpOpen = ref(location.pathname.replace(/\/$/, "") === "/help");
const guideStep = ref<number | null>(null);
let workspaceScroll = 0;
let helpFocusReturn: HTMLElement | null = null;
const dismissedAssignmentWarning = ref("");
const selectedDevices = ref<string[]>([]);
const batchResult = ref<Row | null>(null);
const batchKind = ref("");
const allDevicesSelected = computed(
  () =>
    devices.value.length > 0 &&
    devices.value.every((d) => selectedDevices.value.includes(d.id)),
);
const selectedDeviceRows = computed(() =>
  devices.value.filter((d) => selectedDevices.value.includes(d.id)),
);
const canBatchStop = computed(() =>
  selectedDeviceRows.value.some((d) => d.status !== "stopped"),
);
const canBatchStart = computed(() =>
  selectedDeviceRows.value.some((d) => ["stopped", "fault"].includes(d.status)),
);
const selected = ref<string[]>([]),
  notice = ref(""),
  error = ref(""),
  busy = ref(false),
  loaded = ref(false),
  stale = ref(false),
  lastGood = ref(0);
const feedbackOrigin = ref<HTMLElement | null>(null);
const health = ref<Row>({ storage: {}, endpoints: [] }),
  diagnostics = ref<Row[]>([]),
  onlyErrors = ref(false),
  expandedLog = ref<Row | null>(null);
const connections = ref<Row>({ items: [], active: false });
const assignmentHistory = ref<Row>({ items: [], warning: "" });
const assignmentState = ref("idle");
const assignmentError = ref("");
const dismissedAssignmentError = ref("");
const assignmentDetail = ref<Row | null>(null);
const deviceConnectionCount = computed(
  () => connections.value.items.filter((c: Row) => c.accessed_device).length,
);
const showNav = ref(false),
  showTrend = ref(false),
  pauseChart = ref(false),
  trendData = ref<Row>({}),
  trendKeys = ref<string[]>([]);
const trendHoverX = ref<number | null>(null);
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
const showImportIssues = ref(true);
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
const deviceMenuId = ref("");
const deviceMenuOpen = ref(false);
const globalSettingsOpen = ref(false);
const menuDevice = computed(() =>
  devices.value.find((d) => d.id === deviceMenuId.value),
);
const menuStopped = computed(
  () =>
    menuDevice.value && ["stopped", "fault"].includes(menuDevice.value.status),
);
const storageError = computed(
  () => health.value.storage.error || health.value.storage.history_error || "",
);
const feedbackItems = computed<Feedback[]>(() => {
  const items: Feedback[] = [];
  if (
    tab.value === "assignments" &&
    assignmentError.value &&
    assignmentError.value !== dismissedAssignmentError.value
  )
    items.push({
      id: "assignment-query",
      title: "赋值历史读取失败",
      kind: "alert",
      message: assignmentError.value,
    });
  if (
    tab.value === "assignments" &&
    assignmentHistory.value.warning &&
    assignmentHistory.value.warning !== dismissedAssignmentWarning.value
  )
    items.push({
      id: "assignment-storage",
      title: "赋值历史存储提示",
      kind: "alert",
      message: assignmentHistory.value.warning,
    });
  if (notice.value)
    items.push({
      id: "notice",
      title: "操作提示",
      message: notice.value,
      kind: "status",
    });
  if (error.value)
    items.push({
      id: "error",
      title: "操作失败",
      message: error.value,
      kind: "alert",
    });
  if (modal.value && draftError.value)
    items.push({
      id: "validation",
      title: "校验提示",
      message: draftError.value,
      kind: "alert",
    });
  if (batchResult.value) {
    const result = batchResult.value;
    const verb = result.action === "start" ? "启动" : "停止";
    items.push({
      id: "batch",
      title: `批量${verb}结果`,
      kind: result.failed ? "alert" : "status",
      message:
        `已${verb} ${result.success} 台 · 已处于目标状态 ${result.skipped} 台 · 失败 ${result.failed} 台\n` +
        result.items
          .map(
            (item: Row) =>
              `${item.name}${item.unit_id != null ? `（Unit ${item.unit_id}，${item.host}:${item.port}）` : ""}：${item.message}`,
          )
          .join("\n"),
    });
  }
  const preview = importPreview.value;
  if (
    modal.value === "import" &&
    importStep.value === 2 &&
    preview &&
    showImportIssues.value
  ) {
    if (preview.errors.length)
      items.push({
        id: "import",
        title: "导入校验",
        kind: "alert",
        message:
          `发现 ${preview.errors.length} 个错误，配置尚未应用\n` +
          preview.errors
            .slice(0, 100)
            .map(
              (e: Row) =>
                `${e.sheet} · 第 ${e.row} 行 · ${e.field}：${e.message}`,
            )
            .join("\n") +
          (preview.errors.length > 100 ? "\n其余错误请下载完整清单查看。" : ""),
      });
    else
      items.push({
        id: "import",
        title: "导入校验",
        kind: preview.needs_stop.length ? "alert" : "status",
        message:
          `校验通过：新增 ${preview.added}，更新 ${preview.changed}，删除 ${preview.deleted}，配置版本 ${preview.version}。` +
          (preview.needs_stop.length
            ? "\n" +
              preview.needs_stop.join("\n") +
              "。关闭面板停止设备后，再次校验文件。"
            : "\n配置尚未应用，确认差异后点击应用配置。"),
      });
  }
  if (modal.value === "assign" && valuePreview.value)
    items.push({
      id: "preview",
      title: "赋值校验",
      kind: "status",
      message:
        valuePreview.value.items
          .map(
            (p: Row, i: number) =>
              `${assigned.value[i]?.name}：实际值 ${p.value}，原始寄存器 ${p.raw.join(", ")}`,
          )
          .join("\n") + "\n当前值尚未修改，点击应用当前值后生效。",
    });
  return items;
});
function dismissFeedback(id: string) {
  if (id === "assignment-query") {
    dismissedAssignmentError.value = assignmentError.value;
    return;
  }
  if (id === "assignment-storage") {
    dismissedAssignmentWarning.value = assignmentHistory.value.warning;
    return;
  }
  if (id === "batch") batchResult.value = null;
  else if (id === "notice") notice.value = "";
  else if (id === "error") error.value = "";
  else if (id === "validation") draftError.value = "";
  else if (id === "import") showImportIssues.value = false;
  else if (id === "preview") valuePreview.value = null;
}
async function refreshFeedback(id: string) {
  await action(async () => {
    await refreshConfig();
    if (id === "validation")
      draftError.value = "已获取配置版本，请比较修改后再次保存；原草稿已保留。";
    else await poll();
  });
}
let trendAnchor: HTMLElement | null = null;
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
const deviceCounts = computed(() => {
  const counts: Row = { running: 0, stopped: 0, fault: 0, transition: 0 };
  for (const d of devices.value) {
    const state = d.status in counts ? d.status : "transition";
    counts[state]++;
  }
  return counts;
});
const areaDescriptions: Row = {
  coil: "线圈",
  discrete: "离散输入",
  holding: "保持寄存器",
  input: "输入寄存器",
};
const functionDescriptions: Row = {
  1: "读线圈",
  2: "读离散输入",
  3: "读保持寄存器",
  4: "读输入寄存器",
  5: "写单线圈",
  6: "写单保持寄存器",
  15: "写多个线圈",
  16: "写多个保持寄存器",
  22: "掩码写保持寄存器",
  23: "读写多个保持寄存器",
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
const booleanStrategies = [
  "none",
  "fixed",
  "sequence",
  "replay",
  "expression",
  "alarm",
];
const availableStrategies = computed(() =>
  Object.fromEntries(
    Object.entries(strategyNames).filter(
      ([kind]) =>
        draft.value.type !== "Bool" || booleanStrategies.includes(kind),
    ),
  ),
);
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
        assignments: "赋值记录详情",
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
  return p.value == null
    ? "—"
    : Number(p.value).toFixed(
        ["Float32", "Float64"].includes(p.type) ? (p.precision ?? 2) : 0,
      );
}
function defaultPrecision(type: string) {
  return ["Float32", "Float64"].includes(type) ? 2 : 0;
}
function bytes(value: number) {
  return value ? `${(value / 1048576).toFixed(1)} MiB` : "0 MiB";
}
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}
class RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
async function request(path: string, options: RequestInit = {}) {
  const response = await fetch(path, {
    ...options,
    signal: options.signal ?? AbortSignal.timeout(10000),
    headers:
      options.body instanceof FormData
        ? options.headers
        : { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ message: `请求失败 ${response.status}` }));
    throw new RequestError(
      (body.message ||
        (typeof body.detail === "string" ? body.detail : "") ||
        `请求失败 ${response.status}`) +
        (body.details ? "\n" + JSON.stringify(body.details, null, 2) : ""),
      response.status,
    );
  }
  return response.json();
}
async function action(work: () => Promise<void>) {
  if (busy.value) return;
  const focused = document.activeElement;
  // A busy submit button loses browser focus when disabled; remember it first.
  if (focused instanceof HTMLElement && !focused.closest(".feedback-popover"))
    feedbackOrigin.value = focused;
  busy.value = true;
  batchResult.value = null;
  error.value = "";
  notice.value = "";
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
  selectedDevices.value = selectedDevices.value.filter((id) =>
    devices.value.some((d) => d.id === id),
  );
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
      if (tab.value === "connections") {
        const key = deviceId.value;
        const data = await request(`/api/devices/${key}/connections`);
        if (key === deviceId.value && tab.value === "connections")
          connections.value = data;
      }
      if (tab.value === "assignments") {
        await loadAssignments();
      }
      if (showTrend.value && !pauseChart.value && trendKeys.value.length) {
        const key = deviceId.value;
        const ids = trendKeys.value.join(",");
        const data = await request(`/api/devices/${key}/trends?ids=${ids}`);
        if (
          showTrend.value &&
          key === deviceId.value &&
          ids === trendKeys.value.join(",")
        )
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
  if (!(await closeModal())) return false;
  if (helpOpen.value) await navigateHelp(false);
  closeDeviceMenu();
  deviceId.value = key;
  showNav.value = false;
  return true;
}
watch(deviceId, async (key, previous) => {
  closeTrend();
  closeRuntimeStatus();
  closePacket();
  connections.value = { items: [], active: false };
  assignmentHistory.value = { items: [], warning: "" };
  assignmentState.value = "idle";
  assignmentError.value = dismissedAssignmentError.value = "";
  dismissedAssignmentWarning.value = "";
  assignmentDetail.value = null;
  diagnostics.value = [];
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
watch(tab, () => {
  closeTrend();
  closeRuntimeStatus();
  closePacket();
});
function diagnosticKey(row: Row) {
  return (
    row.id || `${row.time}-${row.host}-${row.transaction_id}-${row.unit_id}`
  );
}
let packetAnchor: HTMLElement | null = null;
async function openPacket(row: Row, event: Event) {
  closeTrend();
  closeRuntimeStatus();
  closeDeviceMenu();
  closeRowMenus();
  packetAnchor = event.currentTarget as HTMLElement;
  expandedLog.value = clone(row);
  await nextTick();
  const popup = document.getElementById("packet-detail-popover");
  if (!popup || !expandedLog.value) return;
  popup.showPopover();
  repositionPacket();
  popup
    .querySelector<HTMLButtonElement>("button")
    ?.focus({ preventScroll: true });
}
function closePacket(restoreFocus = false) {
  const popup = document.getElementById("packet-detail-popover");
  if (popup?.matches(":popover-open")) popup.hidePopover();
  expandedLog.value = null;
  if (restoreFocus)
    (packetAnchor?.isConnected
      ? packetAnchor
      : document.getElementById("diagnostics-tab")
    )?.focus({ preventScroll: true });
}
function packetToggled(event: Event) {
  if ((event as ToggleEvent).newState === "closed") expandedLog.value = null;
}
function packetFocusOut(event: FocusEvent) {
  if (
    !(event.relatedTarget instanceof Node) ||
    !(event.currentTarget as HTMLElement).contains(event.relatedTarget)
  )
    closePacket();
}
function repositionPacket() {
  const popup = document.getElementById("packet-detail-popover");
  const anchor = packetAnchor?.isConnected
    ? packetAnchor
    : document.getElementById("diagnostics-tab");
  if (popup?.matches(":popover-open") && anchor) placePopover(popup, anchor);
}
function packetWindowBlur() {
  closePacket();
}
function packetHex(value: string) {
  return value ? value.match(/.{1,2}/g)?.join(" ") : "无响应";
}
function connectionDuration(since: number) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - since));
  return seconds >= 3600
    ? `${Math.floor(seconds / 3600)}时 ${Math.floor(seconds / 60) % 60}分`
    : seconds >= 60
      ? `${Math.floor(seconds / 60)}分 ${seconds % 60}秒`
      : `${seconds}秒`;
}
function toggleAll() {
  selected.value = allSelected.value ? [] : visibleIds.value.slice();
}
function toggleRow(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((k) => k !== id)
    : [...selected.value, id];
}
function closeDeviceMenu(returnFocus = false) {
  const menu = document.getElementById("device-context-menu");
  if (!menu?.matches(":popover-open")) return;
  menu.hidePopover();
  if (returnFocus)
    document
      .getElementById(`device-card-${deviceMenuId.value}`)
      ?.focus({ preventScroll: true });
}
async function openDeviceMenu(event: MouseEvent | KeyboardEvent, key: string) {
  event.preventDefault();
  closeGlobalSettingsMenu();
  closeRuntimeStatus();
  closeDeviceMenu();
  closeTrend();
  closeRowMenus();
  deviceMenuId.value = key;
  await nextTick();
  const menu = document.getElementById("device-context-menu");
  const card = document.getElementById(`device-card-${key}`);
  if (!menu || !card) return;
  card.focus({ preventScroll: true });
  menu.showPopover();
  const anchor = card.getBoundingClientRect(),
    size = menu.getBoundingClientRect();
  const pointer = event instanceof MouseEvent && event.type === "contextmenu";
  const x = pointer ? event.clientX : anchor.right;
  const y = pointer ? event.clientY : anchor.top;
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - size.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - size.height - 8))}px`;
  menu
    .querySelector<HTMLElement>("button:not(:disabled)")
    ?.focus({ preventScroll: true });
}
function deviceMenuKeyboard(event: KeyboardEvent) {
  contextMenuKeyboard(event, "device-context-menu", closeDeviceMenu);
}
function contextMenuKeyboard(
  event: KeyboardEvent,
  menuId: string,
  close: (returnFocus?: boolean) => void,
) {
  const items = [
    ...document.querySelectorAll<HTMLButtonElement>(
      `#${menuId} button:not(:disabled)`,
    ),
  ];
  const index = items.indexOf(document.activeElement as HTMLButtonElement);
  if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) %
            items.length;
    items[next]?.focus({ preventScroll: true });
  } else if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    close(true);
  } else if (event.key === "Tab") close();
}
function closeGlobalSettingsMenu(returnFocus = false) {
  const menu = document.getElementById("global-settings-menu");
  if (!menu?.matches(":popover-open")) return;
  menu.hidePopover();
  if (returnFocus)
    document
      .getElementById("global-settings-trigger")
      ?.focus({ preventScroll: true });
}
function repositionGlobalSettings() {
  const menu = document.getElementById("global-settings-menu");
  const trigger = document.getElementById("global-settings-trigger");
  if (menu?.matches(":popover-open") && trigger) placePopover(menu, trigger);
}
function prepareGlobalSettingsMenu() {
  const menu = document.getElementById("global-settings-menu");
  if (!menu || modal.value || busy.value) return;
  if (menu.matches(":popover-open")) return;
  closeDeviceMenu();
  closeRowMenus();
  closeTrend();
  closePacket();
  closeRuntimeStatus();
  showNav.value = false;
}
function globalSettingsToggled(event: Event) {
  globalSettingsOpen.value = (event as ToggleEvent).newState === "open";
  if (!globalSettingsOpen.value) return;
  repositionGlobalSettings();
}
async function globalSettingsAction(kind: string) {
  closeGlobalSettingsMenu(true);
  if (kind === "help") await navigateHelp(true);
  else if (kind === "guide") await startGuide();
  else if (kind === "storage") await openStorage();
}
function dismissDeviceMenu(event: PointerEvent) {
  if (
    event.target instanceof Element &&
    event.target.closest(".device-context-menu, .device-menu-trigger")
  )
    return;
  closeDeviceMenu();
}
async function deviceMenuAction(kind: string) {
  const key = deviceMenuId.value;
  if (!menuDevice.value || busy.value) return;
  closeDeviceMenu(true);
  if (["settings", "edit"].includes(kind)) {
    if (!(await selectDevice(key))) return;
    if (kind === "settings") tab.value = "settings";
    else {
      if (innerWidth <= 1050)
        document
          .querySelector<HTMLElement>(".mobile-nav")
          ?.focus({ preventScroll: true });
      await editDevice();
    }
  } else if (kind === "delete") await removeDevice(key);
  else await deviceAction(kind, key);
}
function closeRowMenus(returnFocus = false) {
  for (const menu of document.querySelectorAll<HTMLElement>(
    ".point-menu:popover-open",
  )) {
    menu.hidePopover();
    if (returnFocus)
      document
        .getElementById(menu.id.replace("point-menu-", "point-more-"))
        ?.focus({ preventScroll: true });
  }
}
function positionRowMenu(event: Event, id: string) {
  if ((event as ToggleEvent).newState !== "open") return;
  closeGlobalSettingsMenu();
  closeRuntimeStatus();
  closeDeviceMenu();
  const menu = event.target as HTMLElement;
  const trigger = document.getElementById(`point-more-${id}`);
  if (!trigger) return;
  placePopover(menu, trigger);
}
function placePopover(menu: HTMLElement, trigger: HTMLElement) {
  const anchor = trigger.getBoundingClientRect(),
    popup = menu.getBoundingClientRect();
  const below = anchor.bottom + 6;
  const top =
    below + popup.height <= innerHeight - 8
      ? below
      : anchor.top - popup.height - 6;
  menu.style.left = `${Math.max(8, Math.min(anchor.right - popup.width, innerWidth - popup.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(top, innerHeight - popup.height - 8))}px`;
}
function closeRuntimeStatus() {
  const popup = document.getElementById("device-status-popover");
  if (popup?.matches(":popover-open")) popup.hidePopover();
}
function runtimeStatusToggled(event: Event) {
  if ((event as ToggleEvent).newState !== "open") return;
  closeGlobalSettingsMenu();
  closeDeviceMenu();
  closeRowMenus();
  closeTrend();
  const popup = event.target as HTMLElement;
  const trigger = document.getElementById("device-status-trigger");
  if (trigger) placePopover(popup, trigger);
  popup.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
}
function dismissRowMenus(event: Event) {
  if (
    event.target instanceof Element &&
    event.target.closest(
      ".point-menu, .trend-popover, .device-context-menu, .runtime-status-popover, .feedback-popover",
    )
  )
    return;
  closeRowMenus();
  closeTrend();
  closeDeviceMenu();
  closeRuntimeStatus();
}
async function openModal(kind: string) {
  if (!(await closeModal())) return false;
  closeGlobalSettingsMenu(true);
  closeTrend();
  closePacket();
  closeRuntimeStatus();
  closeDeviceMenu(true);
  closeRowMenus(true);
  focusReturn = document.activeElement as HTMLElement;
  notice.value = "";
  batchResult.value = null;
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
  const wasAssignment = modal.value === "assignments";
  modal.value = "";
  draftError.value = "";
  await nextTick();
  (wasAssignment && !focusReturn?.isConnected
    ? document.getElementById("assignments-tab")
    : focusReturn
  )?.focus({ preventScroll: true });
  return true;
}
function keydown(e: KeyboardEvent) {
  if (
    e.key === "Escape" &&
    document.getElementById("global-settings-menu")?.matches(":popover-open")
  ) {
    e.preventDefault();
    closeGlobalSettingsMenu(true);
    return;
  }
  if (
    e.key === "Escape" &&
    document.getElementById("packet-detail-popover")?.matches(":popover-open")
  ) {
    e.preventDefault();
    closePacket(true);
    return;
  }
  if (
    e.key === "Escape" &&
    document.getElementById("device-context-menu")?.matches(":popover-open")
  ) {
    e.preventDefault();
    closeDeviceMenu(true);
    return;
  }
  if (!modal.value) {
    if (e.key === "Escape" && guideStep.value != null) closeGuide();
    return;
  }
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
async function deviceAction(kind: string, key = deviceId.value) {
  const target = devices.value.find((d) => d.id === key);
  if (!target) return;
  if (
    kind === "stop" &&
    !confirm(`停止 ${target.name} 后该设备不再响应 Modbus 请求，是否继续？`)
  )
    return;
  if (
    kind === "reset" &&
    !confirm(
      `将 ${target.name} 的全部点位恢复初始值、清除保持状态并重新开始策略计时，${target.status === "running" ? "保持通信和设备策略暂停状态" : "保持设备停止状态"}，是否继续？`,
    )
  )
    return;
  await action(async () => {
    await request(`/api/devices/${key}/actions/${kind}`, {
      method: "POST",
    });
    await refreshDevices();
    await loadPoints();
    notice.value =
      kind === "pause"
        ? `${target.name} 已暂停策略，Modbus 仍可读写`
        : kind === "resume"
          ? `${target.name} 已恢复设备策略，点位手动保持仍需单独恢复`
          : kind === "reset"
            ? `${target.name} 已恢复初始值并清除保持；${target.status === "running" ? (target.paused ? "通信继续，设备策略仍暂停" : "通信与策略继续运行") : "设备保持停止"}`
            : `${target.name} 的设备操作已完成`;
  });
}
function toggleAllDevices() {
  selectedDevices.value = allDevicesSelected.value
    ? []
    : devices.value.map((d) => d.id);
}
async function batchDeviceAction(kind: string) {
  const chosen = selectedDeviceRows.value.slice();
  if (!chosen.length || busy.value) return;
  if (
    kind === "stop" &&
    !confirm(
      `停止以下 ${chosen.length} 台设备后，它们不再响应 Modbus 请求，是否继续？\n${chosen.map((d) => `${d.name}（Unit ${d.unit_id}）`).join("\n")}`,
    )
  )
    return;
  closeDeviceMenu();
  closeRuntimeStatus();
  closeTrend();
  closeRowMenus();
  await action(async () => {
    batchKind.value = kind;
    try {
      const result = await request(`/api/devices/actions/${kind}`, {
        method: "POST",
        body: JSON.stringify({ ids: chosen.map((d) => d.id) }),
        signal: AbortSignal.timeout(65000),
      });
      feedbackOrigin.value = document.getElementById("device-select-all");
      batchResult.value = result;
    } catch (e) {
      const message =
        e instanceof DOMException &&
        ["TimeoutError", "AbortError"].includes(e.name)
          ? "批量请求未确认完成，请刷新设备状态后核对结果"
          : (e as Error).message;
      throw new Error(message);
    } finally {
      batchKind.value = "";
      await refreshDevices();
      await loadPoints();
    }
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
async function removeDevice(key = deviceId.value) {
  const target = config.value.devices.find((d: Row) => d.id === key);
  if (!target) return;
  if (!confirm(`删除 ${target.name} 及全部点位和依赖配置？必须先停止设备。`))
    return;
  await action(async () => {
    config.value = await request(
      `/api/devices/${key}?version=${config.value.version}`,
      { method: "DELETE" },
    );
    await refreshDevices();
    await loadPoints();
    deviceMemory.delete(key);
    notice.value = `${target.name} 已删除，配置版本 ${config.value.version} 已生效`;
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
    precision: 0,
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
  if (draft.value.type === "Bool" && draft.value.strategy.kind === "fixed")
    draft.value.strategy.params.value = Number(
      draft.value.strategy.params.value ?? draft.value.initial,
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
  changePointType();
  if (bit) {
    draft.value.scale = 1;
    draft.value.offset = 0;
    draft.value.initial = false;
  }
  if (["discrete", "input"].includes(draft.value.area))
    draft.value.writable = false;
}
function changePointType() {
  draft.value.precision = defaultPrecision(draft.value.type);
  draft.value.scale = 1;
}
function changeStrategy() {
  draft.value.strategy.params = {};
  for (const [key, , value] of parameters[draft.value.strategy.kind] || [])
    draft.value.strategy.params[key] = value;
  draft.value.strategy.dependencies = [];
  paramText.value = "{}";
  if (["ramp", "sequence", "replay"].includes(draft.value.strategy.kind))
    draft.value.strategy.params.loop = true;
  if (["sequence", "replay"].includes(draft.value.strategy.kind))
    sampleText.value = JSON.stringify(
      draft.value.strategy.kind === "sequence"
        ? [
            [5, 0],
            [5, 1],
          ]
        : [
            [0, 0],
            [10, 1],
          ],
      null,
      2,
    );
  if (
    draft.value.strategy.kind !== "none" &&
    draft.value.write_mode === "control"
  )
    draft.value.write_mode = "hold";
}
function changeWriteMode() {
  if (draft.value.write_mode !== "control") return;
  draft.value.strategy = {
    kind: "none",
    enabled: true,
    interval: 1,
    seed: 1,
    params: {},
    dependencies: [],
  };
  paramText.value = "{}";
}
async function savePoint() {
  await action(async () => {
    const point = clone(draft.value);
    if (point.strategy.kind === "thermal") {
      const dependencies = point.strategy.dependencies;
      if (
        dependencies.length !== 2 ||
        new Set(dependencies).size !== 2 ||
        dependencies.some(
          (id: string) =>
            !deviceConfig.value.points.some(
              (p: Row) => p.id === id && p.id !== point.id,
            ),
        )
      )
        throw new Error("请选择两个不同的依赖点位：启动命令和目标温度");
    }
    if (
      point.type === "Bool" &&
      !booleanStrategies.includes(point.strategy.kind)
    )
      throw new Error(
        "该策略不适用于 Bool，请选择固定值、状态序列、回放、表达式或回差报警",
      );
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
    if (point.type === "Bool") {
      const valid = (value: unknown) =>
        value === 0 || value === 1 || value === false || value === true;
      if (
        point.strategy.kind === "fixed" &&
        !valid(point.strategy.params.value)
      )
        throw new Error("布尔点位固定值只能为 0／1");
      if (
        ["sequence", "replay"].includes(point.strategy.kind) &&
        (!Array.isArray(point.strategy.params.values) ||
          point.strategy.params.values.some(
            (row: unknown) => !Array.isArray(row) || !valid(row[1]),
          ))
      )
        throw new Error("布尔点位的序列／回放样本值只能为 0／1");
    }
    const candidate = clone(config.value);
    const d = candidate.devices.find((d: Row) => d.id === deviceId.value);
    const index = d.points.findIndex((p: Row) => p.id === editingId.value);
    if (index >= 0) d.points[index] = point;
    else d.points.push(point);
    await saveConfig(candidate);
    modal.value = "";
    if (device.value?.paused)
      notice.value += "；设备策略仍暂停，请在设备菜单恢复策略";
  });
}
async function deletePoints(ids: string[]) {
  if (
    !confirm(
      `删除 ${ids.length} 个点位？通信保持运行，删除后这些地址将按未配置地址规则响应；被其他点位依赖的点位无法直接删除。`,
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
        origin: "web",
        items: assigned.value.map((p) => ({ id: p.id, value: numericValue() })),
      }),
    });
    await loadPoints();
    modal.value = "";
    notice.value =
      "当前值已修改；初始值保持原配置。操作记录请在设备的“赋值历史”页签查看。";
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
    showImportIssues.value = true;
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
async function viewTrend(point: Row, event?: Event) {
  closeRuntimeStatus();
  closeDeviceMenu();
  trendKeys.value = [point.id];
  trendData.value = {};
  trendHoverX.value = null;
  pauseChart.value = false;
  trendAnchor =
    event?.currentTarget instanceof HTMLElement
      ? event.currentTarget
      : document.getElementById(`point-value-${point.id}`);
  showTrend.value = true;
  await nextTick();
  const popup = document.getElementById("point-trend-popover");
  if (!popup || !trendAnchor) return;
  closeRowMenus();
  trendAnchor.focus({ preventScroll: true });
  popup.showPopover();
  placePopover(popup, trendAnchor);
  await poll();
}
function closeTrend() {
  endTrendDrag();
  const popup = document.getElementById("point-trend-popover");
  if (popup?.matches(":popover-open")) popup.hidePopover();
  showTrend.value = false;
  trendKeys.value = [];
  trendData.value = {};
  trendHoverX.value = null;
}
function trendToggled(event: Event) {
  const popup = event.target as HTMLElement;
  if (popup.matches(":popover-open")) {
    if (trendAnchor) placePopover(popup, trendAnchor);
  } else closeTrend();
}
let trendDrag: {
  pointer: number;
  handle: HTMLElement;
  x: number;
  y: number;
  left: number;
  top: number;
} | null = null;
function moveTrend(left: number, top: number) {
  const popup = document.getElementById("point-trend-popover");
  if (!popup?.matches(":popover-open")) return;
  const bounds = popup.getBoundingClientRect();
  popup.style.left = `${Math.max(8, Math.min(left, innerWidth - bounds.width - 8))}px`;
  popup.style.top = `${Math.max(8, Math.min(top, innerHeight - bounds.height - 8))}px`;
}
function startTrendDrag(event: PointerEvent) {
  if (event.button !== 0) return;
  const handle = event.currentTarget as HTMLElement;
  const popup = document.getElementById("point-trend-popover");
  if (!popup) return;
  const bounds = popup.getBoundingClientRect();
  trendDrag = {
    pointer: event.pointerId,
    handle,
    x: event.clientX,
    y: event.clientY,
    left: bounds.left,
    top: bounds.top,
  };
  handle.setPointerCapture(event.pointerId);
  handle.classList.add("dragging");
  event.preventDefault();
}
function dragTrend(event: PointerEvent) {
  if (!trendDrag || event.pointerId !== trendDrag.pointer) return;
  moveTrend(
    trendDrag.left + event.clientX - trendDrag.x,
    trendDrag.top + event.clientY - trendDrag.y,
  );
}
function endTrendDrag(event?: PointerEvent) {
  if (!trendDrag || (event && event.pointerId !== trendDrag.pointer)) return;
  const { handle, pointer } = trendDrag;
  trendDrag = null;
  handle.classList.remove("dragging");
  if (handle.hasPointerCapture(pointer)) handle.releasePointerCapture(pointer);
}
function keyboardMoveTrend(event: KeyboardEvent) {
  const offsets: Row = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  };
  const delta = offsets[event.key];
  const popup = document.getElementById("point-trend-popover");
  if (!delta || !popup) return;
  event.preventDefault();
  const bounds = popup.getBoundingClientRect();
  const step = event.shiftKey ? 40 : 10;
  moveTrend(bounds.left + delta[0] * step, bounds.top + delta[1] * step);
}
const trendCharts = computed(() =>
  Object.fromEntries(
    trendKeys.value.map((key) => [
      key,
      buildTrendChart(
        trendData.value[key] || [],
        deviceConfig.value?.points.find((p: Row) => p.id === key)?.type ===
          "Bool",
      ),
    ]),
  ),
);
const trendHover = computed(() =>
  trendHoverX.value == null
    ? null
    : nearestTrendPoint(
        trendCharts.value[trendKeys.value[0]]?.points || [],
        trendHoverX.value,
      ),
);
function hoverTrend(event: PointerEvent) {
  const bounds = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
  trendHoverX.value = Math.max(
    20,
    Math.min(680, ((event.clientX - bounds.left) / bounds.width) * 700),
  );
}
function keyboardInspectTrend(event: KeyboardEvent) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const points = trendCharts.value[trendKeys.value[0]]?.points || [];
  if (!points.length) return;
  const current = trendHover.value;
  let index = current ? points.indexOf(current) : -1;
  if (event.key === "Home") index = 0;
  else if (event.key === "End") index = points.length - 1;
  else
    index = Math.max(
      0,
      Math.min(
        points.length - 1,
        index + (event.key === "ArrowRight" ? 1 : -1),
      ),
    );
  trendHoverX.value = points[index].x;
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
function assignmentSource(row: Row) {
  if (row.origin === "web") return "页面赋值";
  if (row.origin === "api") return "API 赋值";
  return "Modbus 写入";
}
async function loadAssignments() {
  const key = deviceId.value;
  if (assignmentState.value === "idle") assignmentState.value = "loading";
  try {
    const data = await request(`/api/devices/${key}/assignments`);
    if (key !== deviceId.value || tab.value !== "assignments") return;
    assignmentHistory.value = data;
    assignmentState.value = "ready";
    assignmentError.value = dismissedAssignmentError.value = "";
  } catch (e) {
    if (key !== deviceId.value || tab.value !== "assignments") return;
    assignmentState.value = "error";
    assignmentError.value =
      e instanceof RequestError && e.status === 404 && e.message === "Not Found"
        ? "当前服务未提供赋值历史接口，请核对访问端口与实际启动的后端版本。读取失败不表示没有赋值记录。"
        : `无法读取赋值历史：${(e as Error).message}。已有记录保留，请重试。`;
  }
}
function assignmentTime(value: number) {
  return new Date(value * 1000).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    fractionalSecondDigits: 3,
  });
}
function assignmentValue(change: Row, side: string) {
  const value = change[side];
  if (value == null) return "非有限值";
  return formatValue({ ...change, value });
}
async function openAssignment(row: Row) {
  if (!(await openModal("assignments"))) return;
  assignmentDetail.value = clone(row);
}
async function navigateHelp(open: boolean) {
  if (!(await closeModal())) return false;
  if (open) {
    workspaceScroll = scrollY;
    helpFocusReturn = document.activeElement as HTMLElement;
    guideStep.value = null;
  }
  closePacket();
  closeTrend();
  closeRuntimeStatus();
  closeDeviceMenu();
  if (helpOpen.value !== open) history.pushState({}, "", open ? "/help" : "/");
  helpOpen.value = open;
  showNav.value = false;
  await nextTick();
  window.scrollTo(0, open ? 0 : workspaceScroll);
  if (open)
    document
      .querySelector<HTMLInputElement>(".help-search input")
      ?.focus({ preventScroll: true });
  else
    (helpFocusReturn?.isConnected
      ? helpFocusReturn
      : document.getElementById("global-settings-trigger")
    )?.focus({ preventScroll: true });
  return true;
}
async function syncHelpRoute() {
  const open = location.pathname.replace(/\/$/, "") === "/help";
  if (open === helpOpen.value) return;
  if (!(await closeModal())) {
    history.pushState({}, "", helpOpen.value ? "/help" : "/");
    return;
  }
  helpOpen.value = open;
  guideStep.value = null;
  closePacket();
  closeTrend();
  closeRuntimeStatus();
  closeDeviceMenu();
  await nextTick();
  window.scrollTo(0, open ? 0 : workspaceScroll);
}
async function startGuide() {
  if (!(await navigateHelp(false))) return;
  tab.value = "monitor";
  guideStep.value = 0;
}
async function closeGuide() {
  guideStep.value = null;
  await nextTick();
  document
    .getElementById("global-settings-trigger")
    ?.focus({ preventScroll: true });
}
function moveGuide(delta: number) {
  if (guideStep.value == null) return;
  guideStep.value = Math.max(
    0,
    Math.min(guideSteps.length - 1, guideStep.value + delta),
  );
  const action = guideSteps[guideStep.value].action;
  tab.value = ["connections", "assignments", "diagnostics"].includes(action)
    ? action
    : "monitor";
  poll();
}
async function guideAction(name: string, event: MouseEvent) {
  if (name === "new") await openModal("new");
  else if (name === "storage") await openStorage();
  else if (name === "device") await editDevice();
  else if (name === "point") await editPoint();
  else if (name === "strategy" && points.value.length)
    await editPoint(points.value[0]);
  else if (name === "control") {
    showNav.value = true;
    await nextTick();
    await openDeviceMenu(event, deviceId.value);
  } else {
    tab.value = name;
    await poll();
  }
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
  window.addEventListener("popstate", syncHelpRoute);
  document.addEventListener("keydown", keydown);
  document.addEventListener("pointerdown", dismissDeviceMenu);
  document.addEventListener("scroll", dismissRowMenus, true);
  window.addEventListener("resize", dismissRowMenus);
  window.addEventListener("resize", repositionPacket);
  window.addEventListener("resize", repositionGlobalSettings);
  window.addEventListener("blur", packetWindowBlur);
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
  window.removeEventListener("popstate", syncHelpRoute);
  endTrendDrag();
  lockBackground(false);
  clearInterval(timer);
  socket?.close();
  document.removeEventListener("keydown", keydown);
  document.removeEventListener("pointerdown", dismissDeviceMenu);
  document.removeEventListener("scroll", dismissRowMenus, true);
  window.removeEventListener("resize", dismissRowMenus);
  window.removeEventListener("resize", repositionPacket);
  window.removeEventListener("resize", repositionGlobalSettings);
  window.removeEventListener("blur", packetWindowBlur);
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
        ><button
          data-guide="new-device"
          aria-label="新建设备"
          @click="openModal('new')"
        >
          ＋
        </button>
      </div>
      <div class="device-summary" aria-label="设备状态汇总">
        <strong>共 {{ devices.length }} 台</strong>
        <span>运行 {{ deviceCounts.running }}</span
        ><span>停止 {{ deviceCounts.stopped }}</span
        ><span>故障 {{ deviceCounts.fault }}</span>
        <span v-if="deviceCounts.transition"
          >切换中 {{ deviceCounts.transition }}</span
        >
      </div>
      <div
        v-if="devices.length"
        class="device-batch-controls"
        aria-label="批量设备操作"
      >
        <label
          ><input
            type="checkbox"
            id="device-select-all"
            aria-label="选择全部设备"
            :checked="allDevicesSelected"
            :indeterminate="selectedDevices.length > 0 && !allDevicesSelected"
            :disabled="busy"
            @change="toggleAllDevices"
          />全选<span>已选 {{ selectedDevices.length }} 台</span></label
        >
        <div class="device-batch-buttons">
          <button
            :disabled="busy || !canBatchStart"
            @click="batchDeviceAction('start')"
          >
            {{ batchKind === "start" ? "启动中…" : "批量启动" }}
          </button>
          <button
            :disabled="busy || !canBatchStop"
            @click="batchDeviceAction('stop')"
          >
            {{ batchKind === "stop" ? "停止中…" : "批量停止" }}
          </button>
        </div>
      </div>
      <nav aria-label="设备列表">
        <div
          v-for="d in devices"
          :key="d.id"
          class="device-card"
          @contextmenu="openDeviceMenu($event, d.id)"
        >
          <input
            type="checkbox"
            class="device-selection"
            :aria-label="`选择设备 ${d.name}`"
            :value="d.id"
            v-model="selectedDevices"
            :disabled="busy"
          />
          <button
            :id="`device-card-${d.id}`"
            class="device-link"
            :data-status="d.status"
            :class="{ active: d.id === deviceId }"
            aria-haspopup="menu"
            :aria-expanded="deviceMenuOpen && deviceMenuId === d.id"
            @click="selectDevice(d.id)"
            @keydown="
              ($event.key === 'ContextMenu' ||
                ($event.shiftKey && $event.key === 'F10')) &&
              openDeviceMenu($event, d.id)
            "
          >
            <strong>{{ d.name }}</strong
            ><span
              >{{ statusNames[d.status]
              }}<template v-if="d.paused"> · 策略暂停</template></span
            ><small>{{ d.host }}:{{ d.port }} / {{ d.unit_id }}</small>
          </button>
          <button
            class="device-menu-trigger"
            :aria-label="`${d.name} 的设备操作菜单`"
            aria-haspopup="menu"
            :aria-expanded="deviceMenuOpen && deviceMenuId === d.id"
            @click="openDeviceMenu($event, d.id)"
          >
            ⋯
          </button>
        </div>
      </nav>
      <div class="sidebar-bottom">
        <span>本机运行 · 后端提供页面</span>
      </div>
    </aside>
    <main v-if="helpOpen" :inert="Boolean(modal)">
      <HelpPage @back="navigateHelp(false)" />
    </main>
    <main v-else :inert="Boolean(modal)">
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
        <div class="workspace-actions">
          <button
            v-if="device"
            id="device-status-trigger"
            class="status-trigger"
            popovertarget="device-status-popover"
            aria-label="查看设备运行状态"
            :title="`${statusNames[device.status]}${stale ? '；数据已过期' : ''}${device.error || storageError ? '；存在异常，请查看详情' : ''}`"
          >
            运行状态<span
              class="status-warning"
              :class="{ visible: stale || device.error || storageError }"
              >异常</span
            >
          </button>
        </div>
      </header>
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
        <nav class="tabs" aria-label="设备功能">
          <button
            :class="{ active: tab === 'monitor' }"
            @click="tab = 'monitor'"
          >
            点位监控</button
          ><button
            :class="{ active: tab === 'connections' }"
            @click="
              tab = 'connections';
              poll();
            "
          >
            连接信息</button
          ><button
            id="diagnostics-tab"
            :class="{ active: tab === 'diagnostics' }"
            @click="
              tab = 'diagnostics';
              poll();
            "
          >
            通信诊断
          </button>
          <button
            id="assignments-tab"
            :class="{ active: tab === 'assignments' }"
            @click="
              tab = 'assignments';
              poll();
            "
          >
            赋值历史
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
                  <th
                    class="mobile-secondary"
                    title="协议地址从 0 开始；Coil：线圈，DI：离散输入，HR：保持寄存器，IR：输入寄存器"
                  >
                    协议地址<span class="cell-note">数据区 · 零起始偏移</span>
                  </th>
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
                      >{{ areaNames[p.area] }} {{ areaDescriptions[p.area] }} ·
                      {{ p.address }} / {{ statusNames[p.state] }}</span
                    >
                  </td>
                  <td class="mono mobile-secondary">
                    {{ areaNames[p.area] }} · {{ p.address
                    }}<span class="cell-note">{{
                      areaDescriptions[p.area]
                    }}</span>
                  </td>
                  <td class="optional">{{ p.type }}</td>
                  <td class="numeric live-value">
                    <button
                      :id="`point-value-${p.id}`"
                      class="text-button current-value"
                      :aria-label="`查看 ${p.name} 的实时趋势`"
                      title="查看此点位实时趋势"
                      @click="viewTrend(p)"
                    >
                      <strong>{{ formatValue(p) }}</strong></button
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
                  <td class="point-operations">
                    <button class="small" @click="openAssign([p])">赋值</button>
                    <button
                      :id="`point-edit-${p.id}`"
                      class="small"
                      :aria-label="`${p.name}：查看／编辑`"
                      @click="editPoint(p)"
                    >
                      查看／编辑
                    </button>
                    <button
                      :id="`point-more-${p.id}`"
                      class="small row-menu"
                      aria-label="更多点位操作"
                      :popovertarget="`point-menu-${p.id}`"
                    >
                      更多
                    </button>
                    <div
                      :id="`point-menu-${p.id}`"
                      popover="auto"
                      class="point-menu"
                      role="group"
                      :aria-label="`${p.name}的点位操作`"
                      @toggle="positionRowMenu($event, p.id)"
                      @click="closeRowMenus(true)"
                    >
                      <button
                        @click="setInitial(p)"
                        :disabled="p.value == null"
                      >
                        当前值设为初始值</button
                      ><button @click="pointAction('pause', [p.id])">
                        暂停策略</button
                      ><button @click="deletePoints([p.id])">删除点位</button>
                    </div>
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
              <option :value="100">100 / 页</option>
            </select>
          </footer>
        </section>
        <section
          v-if="tab === 'monitor'"
          id="point-trend-popover"
          popover="auto"
          class="trend-popover"
          role="dialog"
          aria-modal="false"
          aria-labelledby="trend-title"
          @toggle="trendToggled"
        >
          <div class="section-title">
            <div
              class="trend-drag-handle"
              role="button"
              tabindex="0"
              aria-label="移动实时趋势窗口，方向键移动，Shift 加速"
              @pointerdown="startTrendDrag"
              @pointermove="dragTrend"
              @pointerup="endTrendDrag"
              @pointercancel="endTrendDrag"
              @lostpointercapture="endTrendDrag"
              @keydown="keyboardMoveTrend"
            >
              <h2 id="trend-title">实时趋势</h2>
              <p>拖动标题移动 · 当前点位 · 最近 600 个采样</p>
            </div>
            <div class="button-row">
              <button @click="pauseChart = !pauseChart">
                {{ pauseChart ? "恢复图表刷新" : "暂停图表刷新" }}</button
              ><button autofocus aria-label="关闭实时趋势" @click="closeTrend">
                关闭
              </button>
            </div>
          </div>
          <div v-for="key in trendKeys" :key="key" class="chart">
            <div>
              <strong>{{
                deviceConfig.points.find((p: Row) => p.id === key)?.name
              }}</strong
              ><span
                >{{ trendCharts[key]?.range }}
                {{
                  deviceConfig.points.find((p: Row) => p.id === key)?.unit
                }}</span
              >
            </div>
            <div class="trend-plot">
              <div class="chart-y-axis" aria-label="数值轴">
                <span
                  v-for="(tick, i) in trendCharts[key]?.yTicks"
                  :key="i"
                  :style="{ top: tick.position + '%' }"
                  >{{ tick.label }}</span
                >
              </div>
              <svg
                viewBox="0 0 700 140"
                preserveAspectRatio="none"
                role="img"
                tabindex="0"
                aria-describedby="trend-sample-tooltip"
                @pointermove="hoverTrend"
                @pointerdown="hoverTrend"
                @pointerleave="trendHoverX = null"
                @keydown="keyboardInspectTrend"
                @blur="trendHoverX = null"
                :aria-label="
                  '点位 ' +
                  deviceConfig.points.find((p: Row) => p.id === key)?.name +
                  ' 的实时趋势'
                "
              >
                <path class="axis" d="M20 10V120H690" />
                <path class="signal" :d="trendCharts[key]?.path" />
                <g v-if="trendHover" class="chart-crosshair">
                  <path :d="`M${trendHover.x} 10V120`" />
                  <circle :cx="trendHover.x" :cy="trendHover.y" r="5" />
                </g>
                <circle
                  v-for="(point, i) in trendCharts[key]?.markers"
                  :key="i"
                  class="sample-marker"
                  :cx="point.x"
                  :cy="point.y"
                  r="4"
                >
                  <title>
                    {{ new Date(point.time * 1000).toLocaleString() }} ·
                    {{
                      formatValue({
                        ...deviceConfig.points.find((p: Row) => p.id === key),
                        value: point.value,
                      })
                    }}
                  </title>
                </circle>
              </svg>
              <div
                v-if="trendHover"
                id="trend-sample-tooltip"
                role="tooltip"
                class="chart-tooltip"
              >
                <span>{{
                  new Date(trendHover.time * 1000).toLocaleString()
                }}</span>
                <strong
                  >采样值：{{
                    formatValue({
                      ...deviceConfig.points.find((p: Row) => p.id === key),
                      value: trendHover.value,
                    })
                  }}
                  {{
                    deviceConfig.points.find((p: Row) => p.id === key)?.unit
                  }}</strong
                >
              </div>
              <span v-if="!trendCharts[key]?.path" class="chart-empty">{{
                trendCharts[key]?.count
                  ? "暂无有效数值，非有限值不绘制"
                  : "等待首个采样"
              }}</span>
            </div>
            <div
              class="chart-axis"
              :class="{ single: trendCharts[key]?.ticks.length === 1 }"
              aria-label="时间轴"
            >
              <span v-if="!trendCharts[key]?.ticks.length">等待采样</span>
              <span
                v-for="tick in trendCharts[key]?.ticks"
                :key="tick.index"
                class="chart-tick"
                :class="{ 'mobile-tick': tick.index % 3 === 0 }"
                :title="tick.full"
                ><span v-if="tick.date" class="tick-date">{{ tick.date }}</span
                >{{ tick.label }}</span
              >
            </div>
            <p class="chart-note">
              {{
                device.status === "running"
                  ? device.paused
                    ? "设备策略暂停 · 仍采集当前值"
                    : deviceConfig.faults.freeze
                      ? "策略冻结 · 仍采集当前值"
                      : "实时采样"
                  : "设备已停止 · 仍采集当前值"
              }}
              · {{ trendCharts[key]?.count || 0 }} 个采样
            </p>
          </div>
        </section>
        <section v-if="tab === 'assignments'" class="panel assignment-panel">
          <div class="section-title">
            <div>
              <h2>赋值历史</h2>
              <p>
                <template
                  v-if="
                    assignmentState === 'ready' ||
                    assignmentHistory.items.length
                  "
                  >最近 {{ assignmentHistory.items.length }}／100 条</template
                >
                <template v-else>记录数量待确认</template> · 按时间倒序 ·
                成功和失败均记录
              </p>
            </div>
            <button @click="poll()">刷新记录</button>
          </div>
          <p class="hint">
            记录页面／API 人工赋值和外部 Modbus
            写入，批量操作算一条；策略自动变化不计入。升级前未记录的数值无法追溯。
          </p>
          <div v-if="assignmentHistory.items.length" class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>时间（最新在前）</th>
                  <th>来源</th>
                  <th>结果</th>
                  <th>点位／数量</th>
                  <th>赋值前 → 赋值后</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in assignmentHistory.items" :key="r.id">
                  <td>{{ assignmentTime(r.time) }}</td>
                  <td>
                    <strong>{{ assignmentSource(r) }}</strong
                    ><small v-if="r.host"
                      >{{ r.host }}:{{ r.port }} · 功能码
                      {{ r.function }}</small
                    >
                  </td>
                  <td>
                    <span
                      :class="
                        r.outcome === 'failed'
                          ? 'assignment-failed'
                          : 'assignment-success'
                      "
                      >{{ r.outcome === "failed" ? "失败" : "成功" }}</span
                    >
                  </td>
                  <td>
                    {{
                      r.changes[0]?.name ||
                      (r.address != null
                        ? `${areaNames[r.area]} ${r.address}`
                        : "未解析点位")
                    }}<small v-if="r.count > 1">共 {{ r.count }} 个点位</small>
                  </td>
                  <td>
                    <template v-if="r.outcome === 'failed'">{{
                      r.error
                    }}</template>
                    <template v-else-if="r.changes.length"
                      >{{ assignmentValue(r.changes[0], "before") }} →
                      {{ assignmentValue(r.changes[0], "after") }}
                      {{ r.changes[0].unit
                      }}<small v-if="r.count > 1"
                        >其余点位请查看详情</small
                      ></template
                    >
                  </td>
                  <td>
                    <button
                      :aria-label="`查看 ${r.id} 的赋值详情`"
                      @click="openAssignment(r)"
                    >
                      查看详情
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty">
            <h3>
              {{
                assignmentState === "error"
                  ? "赋值历史暂不可用"
                  : assignmentState !== "ready"
                    ? "正在读取赋值历史…"
                    : "暂无赋值记录"
              }}
            </h3>
            <p v-if="assignmentState === 'ready'">
              执行人工赋值或接收到外部主机写入后，记录会显示在这里。
            </p>
            <p v-else-if="assignmentState === 'error'">
              读取失败，请查看浮动提示并重试。
            </p>
          </div>
        </section>
        <section v-if="tab === 'connections'" class="panel connection-panel">
          <div class="section-title">
            <div>
              <h2>连接信息</h2>
              <p>
                当前设备 Unit {{ device.unit_id }} · {{ device.host }}:{{
                  device.port
                }}
              </p>
            </div>
          </div>
          <div class="connection-summary">
            <strong>已访问当前设备 {{ deviceConnectionCount }} 条</strong>
            <span>监听端点活动连接 {{ connections.items.length }} 条</span>
            <span v-if="!connections.active">当前设备已停止</span>
          </div>
          <p class="connection-hint">
            TCP 连接属于监听端点。同端口多个 Unit ID
            可以共用连接；“已访问当前设备”表示该活动连接发送过当前设备的请求，尚未发送请求的连接无法判定目标设备。
          </p>
          <div v-if="connections.items.length" class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>主机 IP</th>
                  <th>主机端口</th>
                  <th>连接时间 / 时长</th>
                  <th>最近请求</th>
                  <th>最近 Unit</th>
                  <th>当前设备请求</th>
                  <th>访问状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in connections.items" :key="c.id">
                  <td class="mono">{{ c.host }}</td>
                  <td>{{ c.port ?? "—" }}</td>
                  <td>
                    {{
                      new Date(c.connected_at * 1000).toLocaleString("zh-CN", {
                        hourCycle: "h23",
                      })
                    }}<span class="cell-note">{{
                      connectionDuration(c.connected_at)
                    }}</span>
                  </td>
                  <td>
                    {{ c.last_request ? clock(c.last_request) : "尚未请求" }}
                  </td>
                  <td>{{ c.last_unit_id ?? "—" }}</td>
                  <td>{{ c.device_requests }}</td>
                  <td>
                    {{
                      c.accessed_device
                        ? "已访问当前设备"
                        : c.requests
                          ? "其他 Unit／设备"
                          : "尚未请求"
                    }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty compact">
            <h3>暂无活动连接</h3>
            <p>外部主机连接监听端点后会自动显示，连接断开后移除。</p>
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
                <tr v-for="r in diagnostics" :key="diagnosticKey(r)">
                  <td>{{ clock(r.time) }}</td>
                  <td class="mono">{{ r.host }}</td>
                  <td>{{ r.unit_id }}</td>
                  <td>{{ r.function }}</td>
                  <td>{{ r.address }} / {{ r.quantity }}</td>
                  <td>{{ r.ms }}ms</td>
                  <td>
                    <button
                      class="text-button packet-trigger"
                      aria-haspopup="dialog"
                      :aria-expanded="
                        Boolean(
                          expandedLog &&
                            diagnosticKey(expandedLog) === diagnosticKey(r),
                        )
                      "
                      @click="openPacket(r, $event)"
                    >
                      {{ r.error || "正常" }} · 报文
                    </button>
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
        </section>
      </template>
    </main>
    <TourGuide
      v-if="guideStep != null && !modal && !helpOpen"
      :step="guideStep"
      :has-device="Boolean(device)"
      :has-point="points.length > 0"
      :busy="busy"
      @close="closeGuide"
      @next="moveGuide(1)"
      @previous="moveGuide(-1)"
      @action="guideAction"
    />
    <section
      id="packet-detail-popover"
      popover="auto"
      class="packet-popover"
      role="dialog"
      aria-label="报文详情"
      @toggle="packetToggled"
      @focusout="packetFocusOut"
    >
      <template v-if="expandedLog">
        <div class="section-title">
          <h2>报文详情</h2>
          <button @click="closePacket(true)" aria-label="关闭报文详情">
            关闭
          </button>
        </div>
        <p>
          {{
            new Date(expandedLog.time * 1000).toLocaleString("zh-CN", {
              hourCycle: "h23",
            })
          }}
          · {{ expandedLog.host }}
        </p>
        <p>
          事务 {{ expandedLog.transaction_id }} · Unit
          {{ expandedLog.unit_id }} · 功能码 {{ expandedLog.function }} ·
          {{ expandedLog.ms }}ms<br />结果：{{ expandedLog.error || "正常" }}
        </p>
        <h3>请求报文（HEX）</h3>
        <pre tabindex="0" aria-label="请求报文">{{
          packetHex(expandedLog.request)
        }}</pre>
        <h3>响应报文（HEX）</h3>
        <pre tabindex="0" aria-label="响应报文">{{
          packetHex(expandedLog.response)
        }}</pre>
      </template>
    </section>
    <section
      v-if="device"
      id="device-status-popover"
      class="runtime-status-popover"
      popover="auto"
      role="region"
      aria-label="运行状态"
      @toggle="runtimeStatusToggled"
    >
      <header>
        <strong>{{ device.name }} · 运行状态</strong
        ><button
          popovertarget="device-status-popover"
          popovertargetaction="hide"
          aria-label="关闭运行状态"
        >
          关闭
        </button>
      </header>
      <div class="status-details">
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
          <span>最近请求</span><strong>{{ clock(device.last_request) }}</strong>
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
      </div>
      <pre v-if="device.error" class="status-error">{{ device.error }}</pre>
      <pre v-if="storageError" class="status-error">
存储异常：{{ storageError }}</pre
      >
      <button
        @click="
          closeRuntimeStatus();
          tab = 'diagnostics';
          poll();
        "
      >
        查看通信诊断
      </button>
    </section>
    <button
      id="global-settings-trigger"
      class="global-settings-trigger"
      aria-label="全局设置"
      aria-haspopup="menu"
      aria-controls="global-settings-menu"
      popovertarget="global-settings-menu"
      :aria-expanded="globalSettingsOpen"
      :disabled="Boolean(modal) || busy"
      :inert="Boolean(modal)"
      @click="prepareGlobalSettingsMenu"
    >
      设置
    </button>
    <div
      id="global-settings-menu"
      popover="auto"
      class="global-settings-menu"
      role="menu"
      aria-label="全局设置"
      @keydown="
        contextMenuKeyboard(
          $event,
          'global-settings-menu',
          closeGlobalSettingsMenu,
        )
      "
      @toggle="globalSettingsToggled"
    >
      <strong>全局设置</strong>
      <button role="menuitem" autofocus @click="globalSettingsAction('help')">
        使用帮助
      </button>
      <button role="menuitem" @click="globalSettingsAction('guide')">
        使用引导
      </button>
      <button role="menuitem" @click="globalSettingsAction('storage')">
        存储与恢复设置
      </button>
    </div>
    <!-- Manual dismissal prevents the opening right-button release from closing the menu. -->
    <div
      id="device-context-menu"
      popover="manual"
      class="device-context-menu"
      role="menu"
      aria-label="设备操作"
      @keydown="deviceMenuKeyboard"
      @toggle="deviceMenuOpen = ($event as ToggleEvent).newState === 'open'"
    >
      <strong>{{ menuDevice?.name }}</strong>
      <span class="hint">{{
        menuDevice ? statusNames[menuDevice.status] : ""
      }}</span>
      <button
        role="menuitem"
        :disabled="busy || !menuDevice"
        @click="deviceMenuAction('settings')"
      >
        设备设置
      </button>
      <button
        role="menuitem"
        :disabled="busy || !menuStopped"
        @click="deviceMenuAction('start')"
      >
        启动设备
      </button>
      <button
        role="menuitem"
        :disabled="busy || menuDevice?.status !== 'running'"
        @click="deviceMenuAction('stop')"
      >
        停止设备
      </button>
      <button
        role="menuitem"
        :disabled="busy || !menuDevice"
        @click="deviceMenuAction('edit')"
      >
        编辑设备
      </button>
      <button
        role="menuitem"
        :disabled="busy || menuDevice?.status !== 'running'"
        @click="deviceMenuAction(menuDevice?.paused ? 'resume' : 'pause')"
      >
        {{ menuDevice?.paused ? "恢复策略" : "暂停策略" }}
      </button>
      <button
        role="menuitem"
        :disabled="
          busy || !['running', 'stopped', 'fault'].includes(menuDevice?.status)
        "
        @click="deviceMenuAction('reset')"
      >
        重置全部当前值
      </button>
      <button
        role="menuitem"
        :disabled="busy || !menuStopped"
        :title="!menuStopped ? '请先停止设备' : ''"
        @click="deviceMenuAction('delete')"
      >
        删除设备
      </button>
      <span v-if="menuDevice && !menuStopped" class="hint"
        >可运行中重置；删除设备须先停止。</span
      >
    </div>
    <div v-if="modal" class="drawer-layer" @click.self="closeModal">
      <section
        class="drawer"
        :class="{ wide: ['import', 'history', 'assignments'].includes(modal) }"
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
                  ? "可运行中保存或增加点位。原布局兼容的点位保留当前值；新增或修改地址、类型、编码的点位使用初始值，通信保持运行。"
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
                >类型<select
                  aria-label="类型"
                  v-model="draft.type"
                  @change="changePointType"
                >
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
                  :disabled="!['Float32', 'Float64'].includes(draft.type)"
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
                  @change="changeWriteMode"
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
            <p v-if="draft.type === 'Bool'" class="hint">
              布尔点位只接受
              0／1。固定值使用关／开；序列、回放的样本和表达式结果也必须为
              0／1。
            </p>
            <p v-if="draft.write_mode === 'control'" class="hint">
              当前为控制输入。选择生成策略后，写入后行为将切换为“保持写入值”，该点位可按策略自动变化。
            </p>
            <div class="form-grid">
              <label
                >策略类型<select
                  aria-label="策略类型"
                  v-model="draft.strategy.kind"
                  @change="changeStrategy"
                >
                  <option
                    v-if="
                      draft.type === 'Bool' &&
                      !booleanStrategies.includes(draft.strategy.kind)
                    "
                    :value="draft.strategy.kind"
                    disabled
                  >
                    当前策略不适用于 Bool，请重新选择
                  </option>
                  <option
                    v-for="(label, key) in availableStrategies"
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
                />启用此点位策略</label
              ><label
                v-if="draft.type === 'Bool' && draft.strategy.kind === 'fixed'"
                >固定值<select
                  aria-label="固定值"
                  v-model="draft.strategy.params.value"
                >
                  <option :value="0">关（0）</option>
                  <option :value="1">开（1）</option>
                </select></label
              ><label
                v-for="[key, label] in (
                  parameters[draft.strategy.kind] || []
                ).filter(
                  ([key]: string[]) =>
                    !(draft.type === 'Bool' && key === 'value'),
                )"
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
                ['link', 'expression', 'alarm'].includes(draft.strategy.kind)
              "
              >依赖点位<select
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
                  {{
                    draft.strategy.kind === "expression"
                      ? (draft.strategy.dependencies.includes(p.id)
                          ? `x${draft.strategy.dependencies.indexOf(p.id)}`
                          : "未选择") + " · "
                      : ""
                  }}
                  {{ p.name }} · {{ areaNames[p.area] }} {{ p.address }}
                </option>
              </select></label
            >
            <template v-if="draft.strategy.kind === 'thermal'">
              <div class="form-grid">
                <label
                  v-for="(label, index) in ['启动命令依赖', '目标温度依赖']"
                  :key="index"
                >
                  {{ label }}
                  <select
                    :aria-label="label"
                    v-model="draft.strategy.dependencies[index]"
                    required
                  >
                    <option value="" disabled>请选择点位</option>
                    <option
                      v-for="p in deviceConfig.points.filter(
                        (p: Row) => p.id !== draft.id,
                      )"
                      :key="p.id"
                      :value="p.id"
                    >
                      {{ p.name }} · {{ p.type }} · {{ areaNames[p.area] }}
                      {{ p.address }}
                    </option>
                  </select>
                </label>
              </div>
              <p class="hint">
                可自主选择同一设备内的两个不同点位。启动命令非零时向目标温度变化，为零时向环境温度变化；目标温度点位提供设定值，请使用一致的温度单位。
              </p>
            </template>
            <label v-if="draft.strategy.kind === 'expression'"
              >表达式（t、x0、x1 与算术运算）<input
                v-model="draft.strategy.params.expression"
                placeholder="x0 * 0.5 + t"
            /></label>
            <div
              v-if="draft.strategy.kind === 'expression'"
              class="hint"
              aria-label="表达式变量说明"
            >
              <p v-for="(id, index) in draft.strategy.dependencies" :key="id">
                <code>x{{ index }}</code> =
                {{ deviceConfig.points.find((p: Row) => p.id === id)?.name }}
                的当前工程值
              </p>
              <p v-if="!draft.strategy.dependencies.length">
                未选择依赖时，可使用常量和 t。
              </p>
              <p>
                t
                为此点位策略累计运行秒数，停止、暂停或保持时不推进。支持数字、括号与
                +、-、*、/、%（求余），不支持函数、比较或条件表达式。
              </p>
              <p v-if="draft.strategy.dependencies.length >= 2">
                例：(x0 + x1) / 2 计算“{{
                  deviceConfig.points.find(
                    (p: Row) => p.id === draft.strategy.dependencies[0],
                  )?.name
                }}”和“{{
                  deviceConfig.points.find(
                    (p: Row) => p.id === draft.strategy.dependencies[1],
                  )?.name
                }}”的平均值。修改依赖后请核对上方对应关系。
              </p>
              <p v-else-if="draft.strategy.dependencies.length === 1">
                例：x0 * 0.5 为“{{
                  deviceConfig.points.find(
                    (p: Row) => p.id === draft.strategy.dependencies[0],
                  )?.name
                }}”当前工程值的一半。
              </p>
              <p v-else>
                例：20 + t * 0.5 不需要依赖点位，从 20 开始每秒增加 0.5。
              </p>
            </div>
            <label v-if="['sequence', 'replay'].includes(draft.strategy.kind)"
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
                />{{ f }} · {{ functionDescriptions[f] }}</label
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
            <p class="identity-help">
              43 是功能码，14 是 MEI
              类型，表示“读取设备标识”。启用后，客户端可读取下方配置的厂商、产品和版本（对象
              0、1、2），用于识别设备；支持基本读取与单对象读取。不读写点位，不需要新端口；关闭也不影响寄存器读写。
            </p>
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
              ><button @click="showImportIssues = true">
                查看校验结果{{
                  importPreview.errors.length
                    ? `（${importPreview.errors.length} 个错误）`
                    : ""
                }}
              </button>
              <p>
                应用时会重新检查设备状态、引用关系和配置版本，不会自动停机或启动设备。
              </p>
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
          <div
            v-if="modal === 'assignments' && assignmentDetail"
            class="assignment-detail"
          >
            <p>
              {{ assignmentTime(assignmentDetail.time) }} ·
              {{ assignmentSource(assignmentDetail) }} ·
              <strong
                :class="
                  assignmentDetail.outcome === 'failed'
                    ? 'assignment-failed'
                    : 'assignment-success'
                "
                >{{
                  assignmentDetail.outcome === "failed" ? "失败" : "成功"
                }}</strong
              >
            </p>
            <p>
              设备：{{ assignmentDetail.device_name }} · 配置版本
              {{ assignmentDetail.config_version }} ·
              {{ assignmentDetail.count }} 个点位
            </p>
            <p v-if="assignmentDetail.host">
              主机：{{ assignmentDetail.host }}:{{ assignmentDetail.port }} ·
              功能码 {{ assignmentDetail.function }}
            </p>
            <p
              v-if="assignmentDetail.outcome === 'failed'"
              class="assignment-warning"
            >
              失败原因：{{ assignmentDetail.error }}。本次未执行写入。
            </p>
            <p v-if="assignmentDetail.omitted" class="hint">
              大批量操作仅保存前 20 个点位的明细，另有
              {{ assignmentDetail.omitted }} 个点位；数量包含完整操作。
            </p>
            <div
              v-for="c in assignmentDetail.changes"
              :key="c.id"
              class="assignment-change"
            >
              <h3>{{ c.name }}</h3>
              <p>
                {{ areaNames[c.area] }} {{ c.address }} · {{ c.type
                }}<span v-if="c.unit"> · {{ c.unit }}</span>
              </p>
              <p v-if="c.requested != null">请求值：{{ c.requested }}</p>
              <p>
                赋值前：<strong
                  >{{ assignmentValue(c, "before") }} {{ c.unit }}</strong
                ><br />{{
                  assignmentDetail.outcome === "failed"
                    ? "未写入，原值"
                    : "赋值后"
                }}：<strong
                  >{{ assignmentValue(c, "after") }} {{ c.unit }}</strong
                >
              </p>
              <p class="hint">
                原始值：{{ c.before_raw.join(", ") }} →
                {{ c.after_raw.join(", ") }}
              </p>
            </div>
            <p v-if="!assignmentDetail.changes.length" class="hint">
              本次请求没有可解析的现有点位，失败原因和请求信息已记录。
            </p>
            <p v-if="assignmentDetail.requested_ids?.length" class="hint">
              请求点位 ID：{{ assignmentDetail.requested_ids.join(", ") }}
            </p>
            <p v-if="assignmentDetail.request" class="hint">
              请求 PDU（HEX）：{{ assignmentDetail.request }}
            </p>
            <p class="hint">
              这里展示操作当时的记录，列表刷新或点位配置修改不会替换此详情。
            </p>
          </div>
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
    <FeedbackBubbles
      :items="feedbackItems"
      :context="modal"
      :origin="feedbackOrigin"
      @dismiss="dismissFeedback"
    >
      <template #actions="{ item }">
        <button
          v-if="item.id === 'error' || item.id === 'validation'"
          type="button"
          :disabled="busy"
          @click="refreshFeedback(item.id)"
        >
          {{
            item.id === "validation" ? "刷新配置版本并保留草稿" : "重新获取状态"
          }}
        </button>
        <button
          v-if="item.id === 'import' && importPreview?.errors.length"
          type="button"
          @click="errorDownload"
        >
          下载错误清单
        </button>
      </template>
    </FeedbackBubbles>
  </div>
</template>
