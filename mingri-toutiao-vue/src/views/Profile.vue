<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import Button from '@/components/Button.vue';
import { useUserStore } from '@/stores/user';
import { updateNickname } from '@/api/me';
import { THEME_OPTIONS } from '@/constants/theme';

const router = useRouter();
const user = useUserStore();

/** 主题三态：跟随系统 / 浅色 / 深色（store 持久化，useTheme 负责生效） */
const themeOptions = THEME_OPTIONS;

const favCount = computed(() => user.favorites.length);
const historyCount = computed(() => user.history.length);

/**
 * 运营入口仅对管理员展示。
 * `isAdmin` 由登录响应下发（后端按 `ADMIN_PHONES` 判定），
 * 未登录 / 非管理员 / mock 模式下均为 false——入口不会出现。
 */
const showAdminEntry = computed(() => user.isLoggedIn && user.isAdmin);

const menu = computed(() => {
  const items = [
    { key: 'favorites', label: '我的收藏', desc: '查看你保存的内容', to: '/favorites' },
    { key: 'history', label: '浏览历史', desc: '回顾你看过的资讯', to: '/history' },
  ];
  if (showAdminEntry.value) {
    items.push({
      key: 'admin',
      label: 'RSS 源管理',
      desc: '增删启停信源、查看抓取状态',
      to: '/admin/sources',
    });
    items.push({
      key: 'admin-news',
      label: '资讯审核',
      desc: '下架违规资讯、恢复与置顶',
      to: '/admin/news',
    });
    items.push({
      key: 'admin-comments',
      label: '评论治理',
      desc: '隐藏违规评论、恢复与留痕',
      to: '/admin/comments',
    });
    items.push({
      key: 'admin-words',
      label: '敏感词库',
      desc: '维护敏感词、查看命中日志',
      to: '/admin/words',
    });
    items.push({
      key: 'admin-nickname-words',
      label: '昵称敏感词',
      desc: '维护昵称词库、查看命中日志',
      to: '/admin/nickname-words',
    });
    items.push({
      key: 'admin-nickname-reviews',
      label: '昵称复核',
      desc: '人工复核被放行的昵称',
      to: '/admin/nickname-reviews',
    });
    items.push({
      key: 'admin-users',
      label: '管理员管理',
      desc: '指定可访问后台的账号',
      to: '/admin/users',
    });
  }
  items.push({ key: 'settings', label: '设置', desc: '账号与安全', to: '' });
  return items;
});

function go(to: string) {
  if (to) router.push(to);
}

// ------------------------------ 修改昵称（P1-24） ------------------------------

/**
 * 昵称内联编辑。三级结果都由后端把关，前端只做「少跑一轮无效请求」的预校验：
 * - `block`（40006）：拒绝保存，提示**不回显命中词**——文案由后端下发，原样展示；
 * - 形状问题（42203）：空 / 字符集 / 长度，同样是后端文案；
 * - `review`：放行但待人工复核，保存成功的同时明确告知用户「将通过人工复核后展示」；
 * - `pass`：原样保存。
 */
const editing = ref(false);
const draft = ref('');
const saving = ref(false);
const editError = ref('');
/** 保存成功的说明（复核态下它是用户唯一能看到的提示） */
const notice = ref('');

function openEditor() {
  editing.value = true;
  draft.value = user.name;
  editError.value = '';
  notice.value = '';
}

function closeEditor() {
  editing.value = false;
  editError.value = '';
}

async function saveNickname() {
  const next = draft.value.trim();
  if (!next) {
    editError.value = '昵称不能为空';
    return;
  }
  saving.value = true;
  editError.value = '';
  try {
    const result = await updateNickname(next);
    user.setName(result.name);
    notice.value = result.result === 'review' ? '昵称已保存，将通过人工复核后展示' : '昵称已保存';
    closeEditor();
  } catch (e) {
    // 40006 / 42203 的 message 即面向用户的提示，直接展示
    editError.value = e instanceof Error && e.message ? e.message : '保存失败，请稍后重试';
  } finally {
    saving.value = false;
  }
}

async function logout() {
  await user.logout();
  router.push('/');
}
</script>

<template>
  <div class="profile">
    <div class="profile__banner">
      <span class="profile__avatar" />
      <div class="profile__info">
        <span class="profile__name">
          {{ user.isLoggedIn ? user.name || '明日头条用户' : '未登录' }}
          <!-- 改名入口：昵称是账号级属性，注册与改名共用同一条校验链 -->
          <button
            v-if="user.isLoggedIn"
            type="button"
            class="profile__rename"
            aria-label="修改昵称"
            @click="openEditor"
          >
            修改
          </button>
        </span>
        <span class="profile__sub">
          {{ user.isLoggedIn ? '欢迎回来，继续浏览今日热点' : '登录后同步收藏与历史' }}
        </span>
        <span v-if="notice" class="profile__notice" role="status">{{ notice }}</span>
      </div>
    </div>

    <!-- 昵称编辑器：拦截（不回显命中词）/ 形状 / 复核三态都由后端文案驱动 -->
    <div v-if="editing" class="profile__editor" role="dialog" aria-label="修改昵称">
      <p class="profile__editor-title">修改昵称</p>
      <input
        v-model="draft"
        type="text"
        class="profile__editor-input"
        aria-label="新昵称"
        placeholder="2-24 个字符，支持中英文、数字与 _ - · . 等符号"
        maxlength="24"
        @keyup.enter="saveNickname"
      />
      <p class="profile__editor-count">{{ draft.length }}/24</p>
      <p v-if="editError" class="profile__editor-error" role="alert">{{ editError }}</p>
      <div class="profile__editor-actions">
        <Button
          variant="primary"
          size="md"
          :label="saving ? '保存中…' : '保存'"
          :disabled="saving"
          @click="saveNickname"
        />
        <Button
          variant="secondary"
          size="md"
          label="取消"
          :disabled="saving"
          @click="closeEditor"
        />
      </div>
    </div>

    <div class="profile__theme">
      <div class="profile__theme-head">
        <span class="profile__theme-title">外观</span>
        <span class="profile__theme-desc">选择浅色、深色或跟随系统</span>
      </div>
      <div class="profile__segment" role="radiogroup" aria-label="主题模式">
        <button
          v-for="opt in themeOptions"
          :key="opt.value"
          class="profile__segment-item"
          :class="{ 'is-active': user.theme === opt.value }"
          :aria-checked="user.theme === opt.value"
          role="radio"
          @click="user.setTheme(opt.value)"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>

    <div class="profile__menu">
      <button v-for="item in menu" :key="item.key" class="profile__item" @click="go(item.to)">
        <span class="profile__item-main">
          <span class="profile__item-label">{{ item.label }}</span>
          <span class="profile__item-desc">{{ item.desc }}</span>
        </span>
        <span v-if="item.key === 'favorites' && favCount" class="profile__badge">{{
          favCount
        }}</span>
        <span v-else-if="item.key === 'history' && historyCount" class="profile__badge">{{
          historyCount
        }}</span>
        <svg class="profile__item-arrow" width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path
            d="M9 6l6 6-6 6"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>

    <div class="profile__footer">
      <Button variant="secondary" block label="退出登录" @click="logout" />
    </div>
  </div>
</template>

<style scoped>
.profile {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-page-bg);
}

.profile__banner {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 28px 20px;
  background: var(--color-card-bg);
}

.profile__avatar {
  width: 60px;
  height: 60px;
  border-radius: var(--radius-avatar);
  background: linear-gradient(135deg, #4e8bff, #1e4fd8);
  flex-shrink: 0;
}

.profile__info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.profile__name {
  font-size: 19px;
  font-weight: 700;
  color: var(--color-text-primary);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* 「修改」：贴在昵称旁的小入口，不抢主视觉 */
.profile__rename {
  border: 1px solid var(--color-divider);
  border-radius: 999px;
  padding: 2px 10px;
  background: transparent;
  color: var(--color-primary);
  font-size: 12px;
  font-family: var(--font-cn);
  cursor: pointer;
}

/* 保存结果说明：复核态下这是用户唯一能看到的提示 */
.profile__notice {
  font-size: 12px;
  color: #b06000;
}

.profile__sub {
  font-size: 13px;
  color: var(--color-text-secondary);
}

/* 昵称编辑器：底部抽屉，与治理页的确认弹层同款姿态 */
.profile__editor {
  position: sticky;
  bottom: 0;
  margin: 12px 16px 0;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--color-card-bg);
  box-shadow: var(--shadow-card);
}

.profile__editor-title {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.profile__editor-input {
  width: 100%;
  box-sizing: border-box;
  padding: 9px 12px;
  border: 1px solid var(--color-divider);
  border-radius: 8px;
  background: var(--color-page-bg);
  color: var(--color-text-primary);
  font-size: 14px;
  font-family: var(--font-cn);
}

.profile__editor-count {
  margin: 6px 0 0;
  text-align: right;
  font-size: 11px;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}

/* 拦截 / 形状错误：文案由后端下发（40006 不回显命中词），原样展示 */
.profile__editor-error {
  margin: 8px 0 0;
  font-size: 12px;
  color: #d93025;
}

.profile__editor-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.profile__theme {
  padding: 14px 16px;
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

.profile__theme-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 10px;
}

.profile__theme-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.profile__theme-desc {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.profile__segment {
  display: flex;
  gap: 6px;
  padding: 3px;
  border-radius: 10px;
  background: var(--color-page-bg);
}

.profile__segment-item {
  flex: 1;
  padding: 7px 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-family: var(--font-cn);
  cursor: pointer;
}

.profile__segment-item.is-active {
  background: var(--color-card-bg);
  color: var(--color-primary);
  font-weight: 600;
  box-shadow: var(--shadow-card);
}

.profile__menu {
  margin-top: 10px;
  background: var(--color-card-bg);
}

.profile__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 16px 20px;
  border: none;
  background: transparent;
  text-align: left;
  border-bottom: 1px solid var(--color-divider);
  cursor: pointer;
}

/* 末尾箭头：描边用 currentColor，颜色只在这里给（写死灰值不跟主题走） */
.profile__item-arrow {
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.profile__item-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.profile__item-label {
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.profile__item-desc {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.profile__badge {
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-num);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.profile__footer {
  margin-top: auto;
  padding: 20px;
}
</style>
