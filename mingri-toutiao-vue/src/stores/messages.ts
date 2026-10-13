import { defineStore } from 'pinia';
import { computed, ref, watch } from 'vue';
import {
  fetchMessages,
  fetchUnreadCount,
  markAllMessagesRead,
  markMessageRead,
} from '@/api/messages';
import type { Message } from '@/types';

const LS_KEY = 'mingri-toutiao:messages-read';

/** 离线/未登录时也保留已读状态，避免刷新后未读数回退 */
function loadReadIds(): string[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.map(String);
    }
  } catch {
    /* 解析失败回落到空 */
  }
  return [];
}

/**
 * 消息 store：列表来自 /api/messages；
 * 已读状态以本地缓存为准并回写服务端（未登录时仅本地生效）。
 */
export const useMessagesStore = defineStore('messages', () => {
  const list = ref<Message[]>([]);
  const loading = ref(false);
  const error = ref('');
  const readIds = ref<string[]>(loadReadIds());

  const unreadCount = computed(() => list.value.filter((m) => !m.read).length);

  function persistRead() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(readIds.value));
    } catch {
      /* 隐私模式静默失败 */
    }
  }
  watch(readIds, persistRead, { deep: true });

  async function fetch(): Promise<Message[]> {
    loading.value = true;
    error.value = '';
    try {
      const remote = await fetchMessages();
      // 本地已读优先：服务端未登录时 read 恒为 false，不应覆盖本地记录
      list.value = remote.map((m) => (readIds.value.includes(m.id) ? { ...m, read: true } : m));
    } catch (e) {
      error.value = e instanceof Error ? e.message : '消息加载失败';
    } finally {
      loading.value = false;
    }
    return list.value;
  }

  async function markRead(id: string) {
    const item = list.value.find((m) => m.id === id);
    if (item?.read) return;
    if (item) item.read = true;
    if (!readIds.value.includes(id)) readIds.value = [...readIds.value, id];
    try {
      await markMessageRead(id);
    } catch {
      /* 静默：本地已生效 */
    }
  }

  async function markAllRead() {
    const ids = list.value.filter((m) => !m.read).map((m) => m.id);
    if (!ids.length) return;
    list.value = list.value.map((m) => ({ ...m, read: true }));
    readIds.value = Array.from(new Set([...readIds.value, ...ids]));
    try {
      await markAllMessagesRead();
    } catch {
      /* 静默：本地已生效 */
    }
  }

  /** 供 TabBar 徽标使用：轻量拉取未读数（失败静默） */
  async function refreshUnread(): Promise<number> {
    try {
      const remote = await fetchUnreadCount();
      // 未登录时后端返回 0，此时以列表/本地状态为准
      return remote > 0 || list.value.length === 0 ? remote : unreadCount.value;
    } catch {
      return unreadCount.value;
    }
  }

  return { list, loading, error, unreadCount, fetch, markRead, markAllRead, refreshUnread };
});
