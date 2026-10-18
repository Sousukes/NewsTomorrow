import { defineStore } from 'pinia';
import { ref, watch } from 'vue';
import {
  changePassword as apiChangePassword,
  login as apiLogin,
  logout as apiLogout,
} from '@/api/auth';
import { UNAUTHORIZED_EVENT, clearSession, getToken, setSession } from '@/api/http';
import {
  MARKS_SYNC_SUPPORTED,
  addFavorite,
  clearHistoryRemote,
  mergeFavorites,
  mergeHistory,
  recordHistory,
  removeFavorite,
  type MarksPayload,
} from '@/api/me';
import { useNewsStore } from '@/stores/news';
import { toThemeMode, type ThemeMode } from '@/constants/theme';

/** 偏好与登录态（不含标记数据） */
const PREFS_KEY = 'mingri-toutiao:user';
/** 标记数据按**作用域**分键存储：匿名 / 各账号各自独立 */
const MARKS_PREFIX = 'mingri-toutiao:marks:';
const ANON_SCOPE = 'anonymous';
/**
 * 搜索历史**单独分键**，与 marks 分开：
 * 它**不上云**（隐私敏感且跨端价值低），分开存储可确保云端同步逻辑不会误带上它。
 * 作用域规则与 marks 完全一致（复用同一个 scopeKey 后缀）。
 */
const SEARCH_PREFIX = 'mingri-toutiao:search-history:';
const searchKey = (userId: number | null) =>
  `${SEARCH_PREFIX}${userId === null ? ANON_SCOPE : `u${userId}`}`;

/** 浏览历史本地/云端共同遵守的上限（与服务端 HISTORY_MAX 一致） */
export const HISTORY_LIMIT = 50;
/** 搜索历史保留条数 */
export const SEARCH_HISTORY_LIMIT = 10;

interface PrefsShape {
  isLoggedIn: boolean;
  /** 当前账号 id；`null` = 未登录（匿名作用域） */
  userId: number | null;
  theme: ThemeMode;
  /**
   * 是否具备管理能力（后端按 `ADMIN_PHONES` 白名单判定）。
   * 落库是为了刷新页面后入口不消失；白名单在服务端变动时，
   * 该值要等下次登录/续期才更新——届时入口可能短暂失准，
   * 但接口自身仍会 403，不会越权。
   */
  isAdmin: boolean;
  /**
   * 当前账号昵称（P1-24）。
   * 登录响应下发，改名成功后同步；与 isAdmin 同理落库——
   * 刷新页面后「我的」页不退回默认名。服务端改动要等下次登录才可见。
   */
  name: string;
  /**
   * 受限态：管理员刚代其重置过密码（C4）。
   * **必须落盘**——否则刷新页面后守卫失效，用户能到处逛，
   * 直到某个写操作撞上 40305 才想起"我还没改密码"，比一开始就被拦住更困惑。
   */
  mustChangePassword: boolean;
}

interface MarksShape {
  favorites: string[];
  history: string[];
}

/**
 * 空标记。
 *
 * **必须是工厂函数**：早前用共享常量 `{ favorites: [], history: [] }`，
 * 浅拷贝后各作用域拿到的是**同一个数组实例**，一处 push 会污染所有作用域
 * （表现为"换个作用域莫名其妙多了别人的收藏"）。
 */
const emptyMarks = (): MarksShape => ({ favorites: [], history: [] });

const scopeKey = (userId: number | null) =>
  `${MARKS_PREFIX}${userId === null ? ANON_SCOPE : `u${userId}`}`;

function readJson<T>(key: string): Partial<T> | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as Partial<T>) : null;
  } catch {
    return null;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* 隐私模式 / 容量已满时静默失败 */
  }
}

function loadMarks(userId: number | null): MarksShape {
  const parsed = readJson<MarksShape>(scopeKey(userId));
  if (!parsed) return emptyMarks();
  return {
    favorites: Array.isArray(parsed.favorites) ? parsed.favorites.map(String) : [],
    history: Array.isArray(parsed.history) ? parsed.history.map(String) : [],
  };
}

function saveMarks(userId: number | null, marks: MarksShape): void {
  writeJson(scopeKey(userId), marks);
}

function clearMarks(userId: number | null): void {
  try {
    localStorage.removeItem(scopeKey(userId));
  } catch {
    /* 忽略 */
  }
}

/** 读取某作用域的搜索历史（始终返回新数组，避免跨作用域共享实例） */
function loadSearchTerms(userId: number | null): string[] {
  const parsed = readJson<{ terms?: unknown }>(searchKey(userId));
  const raw = parsed?.terms;
  if (!Array.isArray(raw)) return [];
  return raw.filter((x): x is string => typeof x === 'string' && !!x.trim()).map((s) => s.trim());
}

function saveSearchTerms(userId: number | null, terms: string[]): void {
  writeJson(searchKey(userId), { terms });
}

/** 去重且保持首次出现顺序 */
function dedupe(ids: string[]): string[] {
  return Array.from(new Set(ids));
}

/**
 * 读取偏好，并**一次性迁移**旧格式。
 *
 * 旧格式把 `favorites` / `history` 内嵌在 `mingri-toutiao:user` 里且无 `userId`；
 * 直接换新格式会静默丢掉用户已有数据，故迁移到**匿名作用域**
 * （旧数据本就无法归属到某个账号）。
 */
function loadPrefs(): PrefsShape {
  const parsed = readJson<PrefsShape & MarksShape>(PREFS_KEY);

  if (parsed && (Array.isArray(parsed.favorites) || Array.isArray(parsed.history))) {
    // 仅当匿名作用域尚未建立时迁移，避免覆盖新数据
    if (readJson<MarksShape>(scopeKey(null)) === null) {
      saveMarks(null, {
        favorites: (parsed.favorites ?? []).map(String),
        history: (parsed.history ?? []).map(String),
      });
    }
  }

  return {
    isLoggedIn: !!parsed?.isLoggedIn,
    userId: typeof parsed?.userId === 'number' ? parsed.userId : null,
    // 主题单独归一化：历史存档可能没有该字段或值非法
    theme: toThemeMode(parsed?.theme),
    isAdmin: parsed?.isAdmin === true,
    name: typeof parsed?.name === 'string' ? parsed.name : '',
    // C4：受限态落盘；缺省 false（历史存档没有该字段）
    mustChangePassword: parsed?.mustChangePassword === true,
  };
}

/**
 * 用户 store：登录态（含 token）、收藏、浏览历史、主题偏好。
 *
 * **标记数据按用户命名空间隔离**（`mingri-toutiao:marks:<anonymous|u<id>>`）：
 * - 未登录时的收藏/历史落在**匿名作用域**，登出不会丢失；
 * - 各账号的本地缓存互不可见，**不存在换账号串数据**；
 * - 登录时把「匿名期数据 + 该账号本地缓存」**采纳**为该账号数据（匿名作用域随即清空），
 *   再与服务端做**并集合并**；登出仅切回匿名作用域，账号数据仍保留在本地，重新登录即恢复。
 */
export const useUserStore = defineStore('user', () => {
  const prefs = loadPrefs();

  const token = ref(getToken());
  const isLoggedIn = ref(prefs.isLoggedIn && !!token.value);
  /** 当前作用域所属账号；null = 匿名 */
  const userId = ref<number | null>(isLoggedIn.value ? prefs.userId : null);
  /** 管理能力标志（决定是否展示 RSS 源管理等运营入口） */
  const isAdmin = ref(prefs.isAdmin && isLoggedIn.value);
  /** 当前账号昵称；未登录/无记录时为空，界面回落默认名 */
  const name = ref(prefs.name && isLoggedIn.value ? prefs.name : '');
  /** C4：管理员刚代其重置过密码 → 受限态（可读不可写，强制改密后才解除） */
  const mustChangePassword = ref(prefs.mustChangePassword === true && isLoggedIn.value);
  const theme = ref<ThemeMode>(prefs.theme);

  const marks = loadMarks(userId.value);
  const favorites = ref<string[]>(marks.favorites);
  const history = ref<string[]>(marks.history);
  /** 搜索历史：仅本地，按同一套作用域隔离（不同账号不串） */
  const searchTerms = ref<string[]>(loadSearchTerms(userId.value));

  /** 云端同步状态（供界面展示/排障） */
  const syncing = ref(false);
  const syncError = ref('');

  watch(
    [isLoggedIn, userId, theme, isAdmin, name, mustChangePassword],
    () =>
      writeJson(PREFS_KEY, {
        isLoggedIn: isLoggedIn.value,
        userId: userId.value,
        theme: theme.value,
        isAdmin: isAdmin.value,
        name: name.value,
        mustChangePassword: mustChangePassword.value,
      }),
    { deep: true },
  );

  /**
   * 把标记落盘到**当前作用域**。
   *
   * 变更点显式调用（`toggleFavorite` / `addHistory` / `clearHistory` / `applyPayload`），
   * 保证作用域与数据在同一 tick 内一致；下面的 watcher 只作**直接赋值**（如 `$patch`）的兜底。
   * 作用域切换前必须先 `persistMarks()` 把旧作用域落盘（见 `switchScope`），
   * 否则 watcher 会在切换后把旧数据写进新作用域。
   */
  function persistMarks() {
    saveMarks(userId.value, { favorites: favorites.value, history: history.value });
    // 搜索历史随同一作用域落盘（分键存储，但不参与云端同步）
    saveSearchTerms(userId.value, searchTerms.value);
  }

  // 兜底：外部直接赋值（绕过上述方法）时仍能落盘
  watch([favorites, history, searchTerms], persistMarks, { deep: true });

  // 任意请求返回 401（token 过期/失效）→ 清理登录态并切回匿名作用域
  if (typeof window !== 'undefined') {
    window.addEventListener(UNAUTHORIZED_EVENT, () => {
      token.value = '';
      isLoggedIn.value = false;
      isAdmin.value = false;
      name.value = '';
      switchScope(null);
    });
  }

  /** 切换作用域：先把旧作用域落盘，再载入目标作用域的本地缓存 */
  function switchScope(nextUserId: number | null) {
    persistMarks(); // 用**旧** userId 落盘，避免 watcher 之后把旧数据写进新作用域
    const next = loadMarks(nextUserId);
    const nextTerms = loadSearchTerms(nextUserId);
    userId.value = nextUserId;
    favorites.value = next.favorites;
    history.value = next.history;
    searchTerms.value = nextTerms;
  }

  /** 记录一次搜索：去重置顶 + 超限裁剪 */
  function addSearchTerm(term: string) {
    const t = (term ?? '').trim();
    if (!t) return;
    const next = [t, ...searchTerms.value.filter((x) => x !== t)].slice(0, SEARCH_HISTORY_LIMIT);
    searchTerms.value = next;
    persistMarks();
  }

  function removeSearchTerm(term: string) {
    searchTerms.value = searchTerms.value.filter((x) => x !== term);
    persistMarks();
  }

  function clearSearchHistory() {
    searchTerms.value = [];
    persistMarks();
  }

  /** 能同步的前提：构建支持 + 已登录 + 知道是哪个账号 */
  const canSync = () => MARKS_SYNC_SUPPORTED && isLoggedIn.value && userId.value !== null;

  /** 应用服务端返回的集合：写入本地 + 缓存资讯，使收藏/历史页可直接渲染 */
  function applyPayload(payload: MarksPayload, kind: 'favorites' | 'history') {
    if (payload.list.length) useNewsStore().cacheMany(payload.list);
    if (kind === 'favorites') favorites.value = payload.ids;
    else history.value = payload.ids;
    persistMarks();
  }

  /**
   * 与服务端合并本地标记（登录后 / 应用启动时调用）。
   * 失败不抛出：同步是增强能力，不应阻断登录或浏览。
   */
  async function syncMarks(): Promise<void> {
    if (!canSync()) return;
    syncing.value = true;
    syncError.value = '';
    try {
      applyPayload(await mergeFavorites(favorites.value), 'favorites');
      applyPayload(await mergeHistory(history.value), 'history');
    } catch (e) {
      syncError.value = e instanceof Error ? e.message : '云端同步失败';
    } finally {
      syncing.value = false;
    }
  }

  /**
   * 登录：写入 token 与账号作用域，**采纳匿名期数据**，再与服务端并集合并。
   * 匿名数据被采纳后清空匿名作用域（它已归属该账号，留着会造成重复计数）。
   */
  async function login(phone: string, password: string) {
    const res = await apiLogin({ phone, password });
    token.value = res.token;
    // 写入整对令牌（access + refresh + 到期时刻）：登录是唯一一次拿到长令牌的时机
    setSession(res);

    const nextUserId = typeof res.user?.id === 'number' ? res.user.id : null;
    persistMarks(); // 先把当前（匿名）作用域落盘，再切换
    const anon = loadMarks(null);
    const own = loadMarks(nextUserId);

    const adopted: MarksShape = {
      favorites: dedupe([...anon.favorites, ...own.favorites]),
      history: dedupe([...anon.history, ...own.history]).slice(0, HISTORY_LIMIT),
    };

    isLoggedIn.value = true;
    userId.value = nextUserId;
    // 管理能力以后台判定为准：登录即刷新，不沿用上一次会话的旧值
    isAdmin.value = res.user?.isAdmin === true;
    // 昵称同样以登录响应为准（P1-24：改名后下次登录会拿到新值）
    name.value = typeof res.user?.name === 'string' ? res.user.name : '';
    // C4：管理员刚代其重置过密码 → 受限态。落盘让刷新/切路由也能保持约束，
    // 而不只是登录那一刻的一次性提示
    mustChangePassword.value = res.user?.mustChangePassword === true;
    favorites.value = adopted.favorites;
    history.value = adopted.history;
    saveMarks(nextUserId, adopted);
    clearMarks(null);

    await syncMarks();
    return res;
  }

  /** 退出：切回匿名作用域（账号数据保留在本地，重新登录即恢复） */
  async function logout() {
    try {
      await apiLogout();
    } catch {
      /* 网络异常也保证本地退出 */
    }
    token.value = '';
    // 一并清掉 refreshToken：只清 access 会留下一张仍可换发令牌的长令牌（C1）
    clearSession();
    isLoggedIn.value = false;
    isAdmin.value = false;
    name.value = '';
    mustChangePassword.value = false;
    switchScope(null);
  }

  /**
   * 修改密码（C4）。
   *
   * 走二次校验（服务端 40304 会触发全局对话框），成功后：
   * - 后端已把旧令牌全部作废并下发新的一对，这里接着写本地；
   * - 清掉受限标志，用户可以正常发评论/收藏了；
   * - 主动同步一次收藏/历史：受限期间它们的云端写入被拦过，本地数据还没并上去。
   */
  async function changePassword(oldPassword: string, newPassword: string) {
    const res = await apiChangePassword(oldPassword, newPassword);
    token.value = res.token;
    setSession(res);
    mustChangePassword.value = false;
    await syncMarks();
    return res;
  }

  // 序号守卫：并发推送时只应用"最新一次"的响应，避免旧响应覆盖新状态
  let favSeq = 0;
  let histSeq = 0;

  /** 推送收藏变更；失败静默（本地已生效，服务端下次合并会收敛） */
  async function pushFavorite(id: string, adding: boolean) {
    if (!canSync()) return;
    const seq = ++favSeq;
    try {
      const payload = adding ? await addFavorite(id) : await removeFavorite(id);
      if (seq === favSeq) applyPayload(payload, 'favorites');
    } catch (e) {
      syncError.value = e instanceof Error ? e.message : '收藏同步失败';
    }
  }

  function toggleFavorite(id: string) {
    const i = favorites.value.indexOf(id);
    const adding = i < 0;
    if (adding) favorites.value.push(id);
    else favorites.value.splice(i, 1);
    persistMarks();
    void pushFavorite(id, adding);
  }

  function isFavorite(id: string): boolean {
    return favorites.value.includes(id);
  }

  /** 推送浏览记录；失败静默 */
  async function pushHistory(id: string) {
    if (!canSync()) return;
    const seq = ++histSeq;
    try {
      const payload = await recordHistory(id);
      if (seq === histSeq) applyPayload(payload, 'history');
    } catch (e) {
      syncError.value = e instanceof Error ? e.message : '历史同步失败';
    }
  }

  function addHistory(id: string) {
    // 已在最前 → 状态无变化，省掉一次请求
    if (history.value[0] === id) return;
    history.value = [id, ...history.value.filter((h) => h !== id)].slice(0, HISTORY_LIMIT);
    persistMarks();
    void pushHistory(id);
  }

  async function pushClearHistory() {
    if (!canSync()) return;
    const seq = ++histSeq;
    try {
      const payload = await clearHistoryRemote();
      if (seq === histSeq) applyPayload(payload, 'history');
    } catch (e) {
      syncError.value = e instanceof Error ? e.message : '历史同步失败';
    }
  }

  function clearHistory() {
    history.value = [];
    persistMarks();
    void pushClearHistory();
  }

  /** 设置主题（入参统一归一化，非法值回落 `system`） */
  function setTheme(mode: ThemeMode | string) {
    theme.value = toThemeMode(mode);
  }

  /**
   * 更新昵称（P1-24）：改名接口成功后调用。
   * 只改本地展示与持久化——服务端校验（拦截/复核/形状）已在 api 层拦下，
   * 能走到这里说明服务端已接受。
   */
  function setName(next: string) {
    name.value = next;
  }

  return {
    token,
    isLoggedIn,
    userId,
    isAdmin,
    name,
    mustChangePassword,
    favorites,
    history,
    searchTerms,
    theme,
    syncing,
    syncError,
    login,
    logout,
    changePassword,
    syncMarks,
    toggleFavorite,
    isFavorite,
    addHistory,
    clearHistory,
    addSearchTerm,
    removeSearchTerm,
    clearSearchHistory,
    setTheme,
    setName,
  };
});
