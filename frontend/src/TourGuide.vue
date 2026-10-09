<script lang="ts">
export const guideSteps = [
  {
    title: "新建设备",
    target: '[data-guide="new-device"]',
    action: "new",
    label: "打开新建设备",
    text: "从温控模板开始，或创建空设备。名称用于识别，创建不会自动启动通信。",
  },
  {
    title: "设置监听与 Unit ID",
    target: ".endpoint",
    action: "device",
    label: "打开设备编辑",
    text: "本机测试用 127.0.0.1:1502。共享端口使用相同监听 IP、不同 Unit ID；外部主机连接实际电脑 IP。",
  },
  {
    title: "建立点位",
    target: ".monitor-panel .toolbar",
    action: "point",
    label: "打开新增点位",
    text: "选择数据区、类型、协议偏移和初始值，或从工具栏导入 Excel。Float32 占两个寄存器；可运行中增加点位。",
  },
  {
    title: "策略与当前值",
    target: ".monitor-panel tbody tr",
    action: "strategy",
    label: "打开点位编辑",
    text: "点击名称编辑策略，点击赋值修改当前值。依赖框的 xN 和下方说明会随选择更新；保持状态需单独恢复，保存不自动解除设备暂停。",
  },
  {
    title: "控制设备",
    target: ".device-link.active",
    action: "control",
    label: "打开设备菜单",
    text: "右击或点卡片 ⋯，显式启动／停止。暂停策略保留通信；勾选设备可以批量启停。重置可在线执行，删除设备需先停止。",
  },
  {
    title: "观察趋势和主机",
    target: ".tabs",
    action: "connections",
    label: "查看连接信息",
    text: "点击当前值查看单点实时趋势，首次点击开始采集。连接信息展示活动主机及当前 Unit 访问；共享连接不等于所有设备都被访问。",
  },
  {
    title: "检查赋值历史",
    target: ".assignment-panel",
    action: "assignments",
    label: "刷新赋值记录",
    text: "最近 100 条按时间倒序，区分页面／API／Modbus 来源及成功／失败。点击详情查看操作时的前后值，批量写入算一条。",
  },
  {
    title: "排查通信问题",
    target: "#diagnostics-tab",
    action: "diagnostics",
    label: "查看通信诊断",
    text: "查看真实请求、延迟与异常，点击结果展开持久报文气泡。异常时核对地址、功能码、Unit 和权限，必要时限时捕获。",
  },
  {
    title: "配置存储与恢复",
    target: ".sidebar-bottom",
    action: "storage",
    label: "打开存储设置",
    text: "确认实际数据目录；按需要开启历史采样与恢复快照，设置周期、保留和预算。帮助页面可随时查阅全部功能，引导结束不会自动保存或启动。",
  },
];
</script>
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, watch } from "vue";
const props = defineProps<{
  step: number;
  hasDevice: boolean;
  hasPoint: boolean;
  busy: boolean;
}>();
const emit = defineEmits<{
  close: [];
  next: [];
  previous: [];
  action: [name: string, event: MouseEvent];
}>();
const current = computed(() => guideSteps[props.step]);
const canAct = computed(
  () =>
    !props.busy &&
    (["new", "storage"].includes(current.value.action) ||
      (props.hasDevice &&
        (current.value.action !== "strategy" || props.hasPoint))),
);
let target: Element | null = null;
function clearHighlight() {
  target?.classList.remove("tour-target");
  target = null;
}
async function highlight() {
  clearHighlight();
  await nextTick();
  target = document.querySelector(current.value.target);
  if (!target?.getClientRects().length)
    target = document.querySelector("main .page-header");
  target?.classList.add("tour-target");
}
watch(() => [props.step, props.hasDevice, props.hasPoint], highlight);
onMounted(async () => {
  await highlight();
  document
    .querySelector<HTMLButtonElement>(".tour-guide .tour-next")
    ?.focus({ preventScroll: true });
});
onUnmounted(clearHighlight);
</script>
<template>
  <aside
    class="tour-guide"
    role="dialog"
    aria-label="使用引导"
    aria-describedby="tour-description"
    @keydown.esc.stop="emit('close')"
  >
    <div class="tour-header">
      <strong>使用引导 · {{ step + 1 }}／{{ guideSteps.length }}</strong
      ><button aria-label="退出使用引导" @click="emit('close')">退出</button>
    </div>
    <h2>{{ current.title }}</h2>
    <p id="tour-description">{{ current.text }}</p>
    <p v-if="!canAct && !busy" class="hint">
      {{
        !hasDevice
          ? "请先创建一台设备；也可继续阅读后续步骤。"
          : "请先增加点位；也可继续阅读后续步骤。"
      }}
    </p>
    <button :disabled="!canAct" @click="emit('action', current.action, $event)">
      {{ current.label }}
    </button>
    <div class="tour-footer">
      <button :disabled="step === 0" @click="emit('previous')">上一步</button
      ><button
        class="primary tour-next"
        @click="step === guideSteps.length - 1 ? emit('close') : emit('next')"
      >
        {{ step === guideSteps.length - 1 ? "完成引导" : "下一步" }}
      </button>
    </div>
    <small>只提供操作提示，不自动修改配置或数值。</small>
  </aside>
</template>
<style scoped>
.tour-guide {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 100;
  width: min(360px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
  overflow-y: auto;
  padding: 20px;
  border: 1px solid var(--ink);
  border-radius: 24px;
  background: var(--surface);
  box-shadow: 0 4px 16px #0001;
}
.tour-header,
.tour-footer {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: center;
}
.tour-guide h2 {
  font-size: 21px;
  margin: 16px 0 8px;
}
.tour-guide p {
  line-height: 1.7;
}
.tour-guide small {
  display: block;
  margin-top: 12px;
  color: var(--secondary);
}
.tour-footer {
  margin-top: 16px;
}
</style>
