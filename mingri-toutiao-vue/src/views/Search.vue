<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { fetchHotTerms, fetchSuggestions, type HotTerm, type Suggestion } from '@/api/news';
import { useUserStore } from '@/stores/user';

const router = useRouter();
const user = useUserStore();
const keyword = ref('');
const inputEl = ref<HTMLInputElement | null>(null);

/** 热搜：后端真实统计。空数组时不展示该区块——不退回写死的假热词（那是误导） */
const hotTerms = ref<HotTerm[]>([]);

/** 搜索联想的 debounce 间隔（ms）：低于 200ms 等于逐键请求，高于 400ms 用户已等到失去耐心 */
const SUGGEST_DEBOUNCE_MS = 280;
/** 联想条数：与后端默认 limit 一致（超过 20 会被 422，别自己夹取） */
const SUGGEST_LIMIT = 8;

const suggestions = ref<Suggestion[]>([]);
const suggestOpen = ref(false);

onMounted(() => {
  inputEl.value?.focus();
  void fetchHotTerms()
    .then((list) => (hotTerms.value = list))
    .catch(() => (hotTerms.value = []));
});

function go(q: string) {
  const k = q.trim();
  if (!k) return;
  // 收起联想：点击建议 = 提交，不需要再确认一次（D4 定案）
  suggestOpen.value = false;
  debounceTimer.value = undefined;
  // 记录到本地搜索历史（按账号隔离）
  user.addSearchTerm(k);
  router.push({ path: '/search/results', query: { q: k } });
}

function submit() {
  go(keyword.value);
}

/**
 * 输入联想（E2 / P1-70）：debounce + **代际守卫**。
 *
 * 代际守卫不是可选优化：联想是网络请求，而用户可能连续输入多个字符。
 * 不做守卫时，慢的那个请求会先回来、覆盖快请求的结果——表现为
 * 「我明明已经打到第三个字，下拉还是第一个字的词」，且这种错位极难复现。
 *
 * 联想失败静默降级为空：它是输入过程的辅助，坏掉不该影响搜索本身。
 */
const suggestGen = ref(0);
const debounceTimer = ref<ReturnType<typeof setTimeout> | undefined>(undefined);

async function loadSuggestions() {
  const gen = ++suggestGen.value;
  const text = keyword.value.trim();
  if (!text) {
    suggestions.value = [];
    suggestOpen.value = false;
    return;
  }
  const list = await fetchSuggestions(text, SUGGEST_LIMIT);
  if (gen !== suggestGen.value) return; // 已有更新的输入丢弃本次结果
  suggestions.value = list;
  suggestOpen.value = list.length > 0;
}

watch(keyword, (val) => {
  window.clearTimeout(debounceTimer.value);
  if (!val.trim()) {
    suggestGen.value += 1; // 让在途请求作废，别再回来弹出一个空下拉
    suggestions.value = [];
    suggestOpen.value = false;
    return;
  }
  debounceTimer.value = window.setTimeout(() => void loadSuggestions(), SUGGEST_DEBOUNCE_MS);
});

onBeforeUnmount(() => window.clearTimeout(debounceTimer.value));

/** kind → 展示：纠错项明确标出"您是不是想搜"，其余只做图标区分 */
const kindLabel = computed<Record<Suggestion['kind'], string>>(() => ({
  hot: '热搜',
  correct: '纠错',
  title: '标题',
}));
</script>

<template>
  <div class="search">
    <div class="search__bar">
      <button class="search__back" aria-label="返回" @click="router.back()">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <path
            d="M15 5l-7 7 7 7"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <input
        ref="inputEl"
        v-model="keyword"
        class="search__input"
        type="search"
        placeholder="搜索你感兴趣的内容"
        aria-label="搜索"
        role="combobox"
        :aria-expanded="suggestOpen ? 'true' : 'false'"
        aria-controls="search-suggest"
        @keyup.enter="submit"
      />
      <button class="search__go" type="button" @click="submit">搜索</button>
    </div>

    <!-- 输入联想（E2）：只在有内容且结果非空时出现；回车/点外部不拦，原提交路径照旧 -->
    <ul
      v-if="suggestOpen"
      id="search-suggest"
      class="search__suggest"
      role="listbox"
      aria-label="搜索建议"
    >
      <li v-for="s in suggestions" :key="s.term" class="search__suggest-row" role="option">
        <button class="search__suggest-btn" type="button" @click="go(s.term)">
          <span class="search__suggest-term">{{ s.term }}</span>
          <span v-if="s.kind === 'correct'" class="search__suggest-tag search__suggest-tag--fix"
            >您是不是想搜</span
          >
          <span v-else-if="s.kind === 'hot'" class="search__suggest-tag">{{
            kindLabel[s.kind]
          }}</span>
          <span v-if="s.kind === 'hot' && s.hitCount" class="search__suggest-count"
            >{{ s.hitCount }} 次</span
          >
        </button>
      </li>
    </ul>

    <div v-if="hotTerms.length" class="search__section">
      <h3 class="search__h">热门搜索</h3>
      <div class="search__tags">
        <button v-for="t in hotTerms" :key="t.term" class="search__tag" @click="go(t.term)">
          {{ t.term }}
        </button>
      </div>
    </div>

    <div v-if="user.searchTerms.length" class="search__section">
      <div class="search__h-row">
        <h3 class="search__h">历史搜索</h3>
        <button class="search__clear" type="button" @click="user.clearSearchHistory()">清空</button>
      </div>
      <div class="search__tags">
        <div v-for="t in user.searchTerms" :key="t" class="search__tag-wrap">
          <button class="search__tag search__tag--ghost" @click="go(t)">{{ t }}</button>
          <button
            class="search__remove"
            type="button"
            :aria-label="`删除搜索记录 ${t}`"
            @click="user.removeSearchTerm(t)"
          >
            ×
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-page-bg);
  /* 联想浮层以此为定位基准（absolute 的祖先） */
  position: relative;
}

.search__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 52px;
  padding: 0 12px;
  background: var(--color-card-bg);
}

.search__back {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  padding: 0;
  /* 图标一律 currentColor：写死 #222 在深色模式下会与卡片底色几乎同色 */
  color: var(--color-text-primary);
}

.search__input {
  flex: 1;
  height: 36px;
  padding: 0 14px;
  border: none;
  border-radius: 18px;
  background: var(--color-page-bg);
  font-size: 14px;
  color: var(--color-text-primary);
  outline: none;
  font-family: var(--font-cn);
}

.search__input::placeholder {
  color: var(--color-text-secondary);
}

.search__go {
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
}

.search__section {
  padding: 18px 16px;
}

/* 输入联想（E2）：紧贴搜索栏下方，覆盖在内容之上（不是把内容顶下去） */
.search__suggest {
  position: absolute;
  top: 52px;
  left: 0;
  right: 0;
  z-index: 20;
  margin: 0;
  padding: 4px 0;
  list-style: none;
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
  box-shadow: 0 6px 18px rgb(0 0 0 / 12%);
}

.search__suggest-row {
  display: block;
}

.search__suggest-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 16px;
  border: none;
  background: transparent;
  text-align: left;
  font-family: var(--font-cn);
  cursor: pointer;
}

.search__suggest-btn:hover,
.search__suggest-btn:focus-visible {
  background: var(--color-page-bg);
}

.search__suggest-term {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  color: var(--color-text-primary);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.search__suggest-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  font-size: 11px;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 8px;
}

/* 纠错项用强调色标出：它解决的是"输入就错了"，不是"更多候选" */
.search__suggest-tag--fix {
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.search__suggest-count {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.search__h-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.search__h {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.search__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.search__tag {
  height: 32px;
  padding: 0 14px;
  border: none;
  border-radius: 16px;
  background: var(--color-card-bg);
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
}

/* 历史搜索：词条与删除按钮成组，× 贴在标签右侧 */
.search__tag-wrap {
  position: relative;
  display: inline-flex;
}

.search__remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  line-height: 1;
  color: var(--color-text-secondary);
  background: var(--color-card-bg);
  border: 1px solid var(--color-border);
  border-radius: 50%;
  cursor: pointer;
  padding: 0;
}

.search__clear {
  border: none;
  background: transparent;
  font-size: 12px;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 0;
}

.search__tag--ghost {
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}
</style>
