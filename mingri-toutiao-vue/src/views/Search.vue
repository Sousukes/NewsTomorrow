<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { fetchHotTerms, type HotTerm } from '@/api/news';
import { useUserStore } from '@/stores/user';

const router = useRouter();
const user = useUserStore();
const keyword = ref('');
const inputEl = ref<HTMLInputElement | null>(null);

/** 热搜：后端真实统计。空数组时不展示该区块——不退回写死的假热词（那是误导） */
const hotTerms = ref<HotTerm[]>([]);

onMounted(() => {
  inputEl.value?.focus();
  void fetchHotTerms()
    .then((list) => (hotTerms.value = list))
    .catch(() => (hotTerms.value = []));
});

function go(q: string) {
  const k = q.trim();
  if (!k) return;
  // 记录到本地搜索历史（按账号隔离）
  user.addSearchTerm(k);
  router.push({ path: '/search/results', query: { q: k } });
}

function submit() {
  go(keyword.value);
}
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
        @keyup.enter="submit"
      />
      <button class="search__go" type="button" @click="submit">搜索</button>
    </div>

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
