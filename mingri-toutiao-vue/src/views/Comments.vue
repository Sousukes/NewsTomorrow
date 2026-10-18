<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import EmptyState from '@/components/EmptyState.vue';
import Button from '@/components/Button.vue';
import ReportDialog from '@/components/ReportDialog.vue';
import { useCommentsStore, type Comment } from '@/stores/comments';
import { useNewsDetail } from '@/composables/useNewsDetail';
import { COMMENT_SORTS, type CommentSort } from '@/constants/comments';

const route = useRoute();
const router = useRouter();
const commentsStore = useCommentsStore();

const articleId = computed(() => route.params.id as string);
// 深链安全：内存无此条时自动按 id 拉取
const { item, resolving, failed } = useNewsDetail(() => articleId.value);

const list = computed<Comment[]>(() => commentsStore.byArticle[articleId.value] ?? []);
// 评论总数 = 主评论 + 回复（与后端 comment_count 口径一致）
const totalCount = computed(() => commentsStore.count(articleId.value));

onMounted(() => commentsStore.ensure(articleId.value));
watch(articleId, (id) => {
  commentsStore.ensure(id);
  // 换文章：排序跟该文章已选的走（不把上一篇的选择带过去）
  sort.value = commentsStore.sortOf(id);
});

const draft = ref('');

/** 两级评论（P1-12）UI 状态 */
// 已展开完整回复的主评论 id 集合
const expanded = ref<Set<string>>(new Set());
// 回复目标：主评论或其下某条回复（回复的回复拍平到同一主评论下）
const replyTarget = ref<{ id: string; name: string } | null>(null);
const replyDraft = ref('');

/** 排序切换（E6）：latest / hot。列表整体被替换，展开态一并失效 */
const sort = ref<CommentSort>(commentsStore.sortOf(articleId.value));
const sortError = ref('');

async function changeSort(next: CommentSort) {
  if (next === sort.value) return;
  sortError.value = '';
  sort.value = next;
  // 排序变了，列表被整体替换，旧的展开态与回复目标都失去语境
  expanded.value.clear();
  replyTarget.value = null;
  const ok = await commentsStore.setSort(articleId.value, next);
  if (!ok) {
    // 回退：标签必须与列表的真实顺序一致，不能只换个高亮
    sort.value = commentsStore.sortOf(articleId.value);
    sortError.value = '切换排序失败，请稍后重试';
  }
}

/** 展平后的渲染行：主评论 + 其可见回复；最后一条可见回复行携带「查看更多」入口 */
interface Row {
  c: Comment;
  root: Comment;
  isRoot: boolean;
  showMore: boolean;
}

function visibleReplies(root: Comment): Comment[] {
  const all = root.replies ?? [];
  // 未展开 = 列表接口的「最早 2 条」预览；展开后 = 完整列表
  return expanded.value.has(root.id) ? all : all.slice(0, 2);
}

const rows = computed<Row[]>(() =>
  list.value.flatMap((root) => {
    const vis = visibleReplies(root);
    const out: Row[] = [{ c: root, root, isRoot: true, showMore: vis.length === 0 }];
    vis.forEach((r, i) => out.push({ c: r, root, isRoot: false, showMore: i === vis.length - 1 }));
    return out;
  }),
);

const palette = [
  'linear-gradient(135deg,#c9cdd4,#aeb4bd)',
  'linear-gradient(135deg,#b9c6e8,#8ea6dd)',
  'linear-gradient(135deg,#cdd6e0,#a8b8cf)',
  'linear-gradient(135deg,#c2c9d6,#9aa6bd)',
];

function avatar(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
}

function toggleLike(c: Comment) {
  commentsStore.toggleLike(articleId.value, c.id);
}

function startReply(c: Comment) {
  replyTarget.value = { id: c.id, name: c.name };
  replyDraft.value = '';
}

function cancelReply() {
  replyTarget.value = null;
  replyDraft.value = '';
}

function sendReply() {
  const text = replyDraft.value.trim();
  if (!text || !replyTarget.value) return;
  commentsStore.reply(articleId.value, replyTarget.value.id, '我', text);
  replyDraft.value = '';
  replyTarget.value = null;
}

async function toggleExpand(root: Comment) {
  if (expanded.value.has(root.id)) {
    expanded.value.delete(root.id);
    return;
  }
  expanded.value.add(root.id);
  await commentsStore.loadReplies(articleId.value, root.id);
}

function loadMore(root: Comment) {
  void commentsStore.loadMoreReplies(articleId.value, root.id);
}

/** 删除失败提示（403 等非预期场景） */
const deleteError = ref('');

async function remove(c: Comment) {
  if (!window.confirm('确定删除这条评论吗？删除后不可恢复。')) return;
  deleteError.value = '';
  const ok = await commentsStore.remove(articleId.value, c.id);
  if (!ok) deleteError.value = '删除失败，请稍后重试';
}

function send() {
  const text = draft.value.trim();
  if (!text) return;
  commentsStore.add(articleId.value, '我', text);
  draft.value = '';
}

/**
 * 举报入口（E5）：只对**他人**的评论显示。
 * 举报自己既无意义，又会让服务端多一条必然被驳回的记录（还会占复核队列）。
 */
const reportOpen = ref(false);
const reportTargetId = ref('');

function openReport(c: Comment) {
  reportTargetId.value = c.id;
  reportOpen.value = true;
}
</script>

<template>
  <div v-if="item" class="comments">
    <AppNav variant="title" title="评论" show-back @back="router.back()" />

    <div class="comments__head">
      <span v-if="totalCount" class="comments__count">共 {{ totalCount }} 条评论</span>
      <span v-else class="comments__count" />
      <div class="comments__sorts" role="tablist" aria-label="评论排序">
        <button
          v-for="s in COMMENT_SORTS"
          :key="s.key"
          class="comments__sort"
          :class="{ 'is-active': sort === s.key }"
          role="tab"
          :aria-selected="sort === s.key"
          @click="changeSort(s.key)"
        >
          {{ s.label }}
        </button>
      </div>
    </div>
    <div v-if="sortError" class="comments__error" role="alert">{{ sortError }}</div>
    <div v-if="deleteError" class="comments__error" role="alert">{{ deleteError }}</div>

    <div class="comments__list">
      <template v-for="row in rows" :key="row.c.id">
        <div class="cmt" :class="{ 'cmt--reply': !row.isRoot }">
          <span
            class="cmt__avatar"
            :class="{ 'cmt__avatar--sm': !row.isRoot }"
            :style="{ backgroundImage: avatar(row.c.seed) }"
          />
          <div class="cmt__body">
            <div class="cmt__head">
              <span class="cmt__name">{{ row.c.name }}</span>
              <div class="cmt__head-right">
                <span class="cmt__time">{{ row.c.time }}</span>
                <!-- 仅自己的评论显示删除入口（服务端仍会二次校验归属） -->
                <button
                  v-if="row.c.mine"
                  class="cmt__delete"
                  :aria-label="`删除评论：${row.c.content.slice(0, 10)}`"
                  @click="remove(row.c)"
                >
                  删除
                </button>
              </div>
            </div>
            <p class="cmt__content">
              <span v-if="row.c.replyToName" class="cmt__replyto"
                >回复 @{{ row.c.replyToName }}：</span
              >{{ row.c.content }}
            </p>
            <div class="cmt__actions">
              <button
                class="cmt__like"
                :class="{ 'is-liked': row.c.liked }"
                @click="toggleLike(row.c)"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path
                    d="M12 21s-7-4.4-9.5-8.5C1 9.5 2.5 6 6 6c2 0 3 1 4 2 1-1 2-2 4-2 3.5 0 5 3.5 3.5 6.5C19 16.6 12 21 12 21z"
                    :stroke="row.c.liked ? '#e5484d' : '#9a9fa8'"
                    :fill="row.c.liked ? '#e5484d' : 'none'"
                    stroke-width="1.6"
                    stroke-linejoin="round"
                  />
                </svg>
                <span>{{ row.c.likes }}</span>
              </button>
              <button class="cmt__reply-btn" @click="startReply(row.c)">回复</button>
              <!-- 举报他人评论（E5）：自己的评论不显示（删掉即可，不需要举报自己） -->
              <button
                v-if="!row.c.mine"
                class="cmt__report-btn"
                :aria-label="`举报评论：${row.c.content.slice(0, 10)}`"
                @click="openReport(row.c)"
              >
                举报
              </button>
            </div>

            <!-- 回复输入框：跟随当前回复目标（主评论或其下某条回复） -->
            <div v-if="replyTarget?.id === row.c.id" class="cmt__replybox">
              <input
                v-model="replyDraft"
                class="cmt__replybox-input"
                type="text"
                :placeholder="`回复 @${replyTarget.name}`"
                :aria-label="`回复 @${replyTarget.name}`"
                @keyup.enter="sendReply"
              />
              <Button
                variant="primary"
                label="发送"
                :disabled="!replyDraft.trim()"
                @click="sendReply"
              />
              <button class="cmt__replybox-cancel" @click="cancelReply">取消</button>
            </div>

            <!-- 回复区的「查看全部 / 加载更多 / 收起」挂在最后一条可见回复行上 -->
            <div v-if="row.showMore && row.isRoot && row.root.replyCount" class="cmt__replies-more">
              <button class="cmt__more-btn" @click="toggleExpand(row.root)">
                查看全部 {{ row.root.replyCount }} 条回复
              </button>
            </div>
            <div
              v-else-if="
                row.showMore && !row.isRoot && expanded.has(row.root.id) && row.root.replyCount
              "
              class="cmt__replies-more"
            >
              <button
                v-if="commentsStore.repliesMeta[row.root.id]?.hasMore"
                class="cmt__more-btn"
                :disabled="commentsStore.repliesMeta[row.root.id]?.loading"
                @click="loadMore(row.root)"
              >
                {{ commentsStore.repliesMeta[row.root.id]?.loading ? '加载中…' : '加载更多回复' }}
              </button>
              <button class="cmt__more-btn" @click="toggleExpand(row.root)">收起</button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <EmptyState
      v-if="!list.length"
      icon="message"
      title="还没有评论"
      description="来说两句，开启第一条评论"
    >
      <Button variant="primary" label="去评论" @click="send" />
    </EmptyState>

    <div class="comments__bar">
      <input
        v-model="draft"
        class="comments__input"
        type="text"
        placeholder="写下你的评论…"
        aria-label="评论输入"
        @keyup.enter="send"
      />
      <Button variant="primary" label="发送" :disabled="!draft.trim()" @click="send" />
    </div>

    <!-- 举报弹窗（E5）：目标 id 随点击的评论切换，弹窗内部自己处理登录/提交 -->
    <ReportDialog v-model:open="reportOpen" target-type="comment" :target-id="reportTargetId" />
  </div>

  <div v-else-if="resolving" class="comments-loading">加载中…</div>

  <EmptyState
    v-else
    icon="network"
    :title="failed ? '加载失败，请稍后重试' : '内容不存在或已下架'"
    :description="
      failed ? '网络或服务暂时不可用，检查网络后重试' : '该资讯可能已被删除，换条新闻看看吧'
    "
  >
    <Button variant="primary" label="返回首页" @click="router.push('/')" />
  </EmptyState>
</template>

<style scoped>
.comments {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-page-bg);
}

.comments__count {
  padding: 10px 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

/* 计数 + 排序同一行：排序是列表的视图选项，应该贴着列表而不是浮在页头 */
.comments__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

.comments__head .comments__count {
  border-bottom: none;
  background: transparent;
}

.comments__sorts {
  display: flex;
  align-items: center;
  gap: 4px;
  padding-right: 12px;
  flex-shrink: 0;
}

.comments__sort {
  border: none;
  background: transparent;
  padding: 6px 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-radius: 12px;
}

.comments__sort.is-active {
  color: var(--color-primary);
  font-weight: 600;
}

.comments__list {
  flex: 1;
  overflow-y: auto;
  background: var(--color-card-bg);
}

.cmt {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-divider);
}

/* 回复行：左缩进 + 小头像，视觉上从属于主评论 */
.cmt--reply {
  padding: 10px 16px 10px 44px;
  background: var(--color-page-bg);
}

.cmt--reply:last-child {
  border-bottom: none;
}

.cmt__avatar {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-avatar);
  background-size: cover;
  background-position: center;
}

.cmt__avatar--sm {
  width: 28px;
  height: 28px;
}

.cmt__body {
  flex: 1;
  min-width: 0;
}

.cmt__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.cmt__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.cmt__head-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cmt__time {
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}

.cmt__delete {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.cmt__delete:hover {
  color: var(--color-error);
}

.comments__error {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--color-error);
  background: var(--color-error-bg);
  border-bottom: 1px solid var(--color-divider);
}

.cmt__content {
  margin: 6px 0 10px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-text-primary);
}

.cmt__replyto {
  color: var(--color-text-secondary);
}

.cmt__actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.cmt__like {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-family: var(--font-num);
  cursor: pointer;
}

.cmt__like.is-liked {
  color: var(--color-error);
}

.cmt__reply-btn,
.cmt__report-btn {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.cmt__reply-btn:hover {
  color: var(--color-brand, #3b6cff);
}

/* 举报是负向操作：hover 用警示色，与「回复」区分开 */
.cmt__report-btn:hover {
  color: var(--color-error);
}

/* 回复输入框 */
.cmt__replybox {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.cmt__replybox-input {
  flex: 1;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: 17px;
  background: var(--color-card-bg);
  font-size: 13px;
  color: var(--color-text-primary);
  outline: none;
  font-family: var(--font-cn);
}

.cmt__replybox-cancel {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}

/* 查看全部 / 加载更多 / 收起 */
.cmt__replies-more {
  display: flex;
  gap: 16px;
  margin-top: 8px;
}

.cmt__more-btn {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-brand, #3b6cff);
  font-size: 12px;
  cursor: pointer;
}

.cmt__more-btn:disabled {
  color: var(--color-text-secondary);
  cursor: default;
}

.comments-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60%;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.comments__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: var(--color-card-bg);
  border-top: 1px solid var(--color-divider);
  flex-shrink: 0;
}

.comments__input {
  flex: 1;
  height: 40px;
  padding: 0 14px;
  border: 1px solid var(--color-border);
  border-radius: 20px;
  background: var(--color-page-bg);
  font-size: 14px;
  color: var(--color-text-primary);
  outline: none;
  font-family: var(--font-cn);
}

.comments__input::placeholder {
  color: var(--color-text-secondary);
}
</style>
