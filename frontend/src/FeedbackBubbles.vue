<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from "vue";

export type Feedback = {
  id: string;
  title: string;
  message: string;
  kind: "status" | "alert";
};
const props = defineProps<{
  items: Feedback[];
  context: string;
  origin: HTMLElement | null;
}>();
const emit = defineEmits<{ dismiss: [id: string] }>();
const popup = ref<HTMLElement | null>(null);
let timer: number | undefined;
let focusReturn: HTMLElement | null = null;
async function dismiss(id: string) {
  const element = popup.value;
  const focused = element?.contains(document.activeElement);
  if (props.items.length === 1 && element?.matches(":popover-open"))
    element.hidePopover();
  emit("dismiss", id);
  await nextTick();
  if (focused) {
    const target =
      popup.value?.querySelector<HTMLElement>("button") || focusReturn;
    if (target?.isConnected) target.focus({ preventScroll: true });
  }
}
function pause() {
  window.clearTimeout(timer);
}
async function resume() {
  pause();
  await nextTick();
  if (
    !props.items.some((item) => item.id === "notice") ||
    popup.value?.matches(":hover") ||
    popup.value?.contains(document.activeElement)
  )
    return;
  timer = window.setTimeout(() => dismiss("notice"), 6000);
}
watch(
  () => [props.items, props.context],
  async () => {
    await nextTick();
    const element = popup.value;
    if (!element?.isConnected) return;
    if (props.items.length && !element.contains(document.activeElement)) {
      const active = document.activeElement as HTMLElement;
      focusReturn =
        active &&
        active !== document.body &&
        !active.closest(".feedback-popover")
          ? active
          : props.origin?.isConnected
            ? props.origin
            : active;
    }
    if (props.items.length && !element.matches(":popover-open"))
      element.showPopover();
    else if (!props.items.length && element.matches(":popover-open"))
      element.hidePopover();
  },
  { immediate: true, flush: "post" },
);
watch(() => props.items.find((item) => item.id === "notice")?.message, resume);
onUnmounted(pause);
</script>

<template>
  <Teleport :to="context ? '.drawer' : '.app-shell'" defer>
    <div
      ref="popup"
      class="feedback-popover"
      popover="manual"
      @mouseenter="pause"
      @mouseleave="resume"
      @focusin="pause"
      @focusout="resume"
    >
      <section
        v-for="item in items"
        :key="item.id"
        class="feedback-bubble"
        :class="{ error: item.kind === 'alert' }"
        :role="item.kind"
        :aria-label="item.title"
        aria-atomic="true"
        @keydown.esc.stop.prevent="dismiss(item.id)"
      >
        <header>
          <strong>{{ item.title }}</strong>
          <button
            type="button"
            :aria-label="`关闭${item.title}`"
            @click="dismiss(item.id)"
          >
            关闭
          </button>
        </header>
        <pre>{{ item.message }}</pre>
        <div class="feedback-actions"><slot name="actions" :item="item" /></div>
      </section>
    </div>
  </Teleport>
</template>
