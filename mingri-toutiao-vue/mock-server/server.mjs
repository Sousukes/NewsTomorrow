/**
 * 明日头条 · 本地 Mock 后端（零依赖，Node 原生 http）
 *
 * 用途：在真实后端就绪前，把前端「真实 HTTP 链路」完整跑通——
 * token 注入 / 鉴权 / 错误码 / 评论 CRUD 均可验证。
 *
 * 启动：npm run mock:api        （默认 3001，可用 MOCK_PORT 覆盖）
 * 前端联调：npm run dev:api     （以 --mode api 启动，自动指向本服务）
 *
 * 约定：所有响应形如 { code, message, data }，code 0/200 为成功。
 */
import { createServer } from 'node:http';

const PORT = Number(process.env.MOCK_PORT || 3001);

/* ----------------------------- mock 数据 ----------------------------- */

const news = [
  {
    id: 'n1',
    title: '国产大飞机再添新航线，商业运营迈出关键一步',
    summary:
      '今日，国产大型客机在多条新航线上正式投入商业运营，标志着我国民用航空制造能力迈上新台阶。',
    source: '新华社',
    commentCount: 320,
    time: '2小时前',
    images: ['', '', ''],
    channel: 'tech',
    variant: 'triple',
    // P1-20：管理端排序/定位用（published_at 统一格式，coalesce 空串）
    publishedAt: '2026-10-15T09:30:00',
    hotScore: 86.5,
  },
  {
    id: 'n2',
    title: '多地出台楼市新政，刚需购房门槛再降低',
    summary: '近期多个城市优化住房信贷政策，首套房贷利率与首付比例同步下调，刚需家庭受益明显。',
    source: '财经周刊',
    commentCount: 156,
    time: '3小时前',
    cover: '',
    channel: 'finance',
    variant: 'left-right',
    publishedAt: '2026-10-15T08:30:00',
    hotScore: 41.2,
  },
  {
    id: 'n3',
    title: '中央气象台发布暴雨蓝色预警，这些地区需注意防范',
    summary: '未来24小时，华南、江南南部等地将出现大到暴雨，局地伴有雷暴大风，请提前做好排涝准备。',
    source: '中国天气',
    commentCount: 89,
    time: '1小时前',
    channel: 'recommend',
    isTop: true,
    variant: 'text',
    publishedAt: '2026-10-15T10:30:00',
    hotScore: 30.75,
  },
  {
    id: 'n4',
    title: '现场直击：国产航母编队展开远海实战化训练',
    summary: '编队连续航行数昼夜，完成舰载机起降、编队机动等多课目训练，体系作战能力得到检验。',
    source: '军事频道',
    playCount: 128000,
    time: '5小时前',
    cover: '',
    channel: 'video',
    variant: 'video',
    // 与后端 to_out 一致：详情/列表都带 commentCount（冗余计数列）。
    // 种子 6 条评论里 c3 已被演示隐藏 → 当前可见 5；隐藏/恢复演示条时
    // 此值随 -1/+1 联动（契约检查第 15 段据此断言计数回退）
    commentCount: 5,
    publishedAt: '2026-10-15T06:30:00',
    hotScore: 58.125,
  },
  {
    id: 'n5',
    title: '新能源汽车出口连续三月创新高，海外市场持续扩容',
    summary: '前五月新能源汽车出口同比大幅增长，欧洲与东南亚成为主要增量市场。',
    source: '新华财经',
    commentCount: 245,
    time: '4小时前',
    images: ['', '', ''],
    channel: 'tech',
    variant: 'triple',
    publishedAt: '2026-10-15T07:30:00',
    hotScore: 63.4,
  },
  {
    id: 'n6',
    title: '教育部：推动义务教育阶段课后服务全覆盖',
    summary: '各地统筹校内外资源，丰富科普、体育、艺术等课程，缓解家长接送与看护压力。',
    source: '人民日报',
    commentCount: 512,
    time: '6小时前',
    cover: '',
    channel: 'recommend',
    variant: 'left-right',
    publishedAt: '2026-10-15T05:30:00',
    hotScore: 91.25,
  },
  {
    // P1-20：已下架种子。公开四路（列表/详情/搜索/评论）实时过滤掉它，
    // 管理端默认 status=all 能看见——否则 mock 下永远看不到「已下架」分支
    id: 'n7',
    title: '演示用已下架资讯',
    summary: '这条资讯在 mock 启动时即处于已下架状态，用于演示审核页的已下架分支与恢复操作。',
    source: '演示源',
    commentCount: 5,
    time: '8小时前',
    channel: 'recommend',
    variant: 'text',
    publishedAt: '2026-10-15T03:30:00',
    hotScore: 3.5,
    hidden: true,
    hiddenAt: '2026-10-15T10:05:00',
    hiddenReason: '演示种子：内容待核实',
  },
];

/**
 * 文章 id → 评论列表（内存，可增删改）。
 * 两级评论（P1-12，与后端一致）：rootId 为 null 的是主评论；回复的 rootId
 * 指向所属主评论（回复的回复拍平到同一 root）。`_seq` 为创建序号——
 * 主列表按它倒序（新的在前），回复按它正序（讨论先后）。
 */
let commentSeq = 100;
const comments = {
  n4: [
    {
      id: 'c1',
      name: '观察者小张',
      seed: 'c1',
      content: '这篇报道角度很扎实，把关键节点都讲清楚了，期待后续跟进。',
      time: '1小时前',
      createdAt: '2026-10-15T09:00:00',
      likes: 86,
      liked: false,
      userId: null,
      mine: false,
      parentId: null,
      rootId: null,
      _seq: 1,
    },
    {
      id: 'c1r1',
      name: '沿海居民',
      seed: 'c1r1',
      content: '同感，数据那一节特别清晰。',
      time: '52分钟前',
      createdAt: '2026-10-15T09:20:00',
      likes: 6,
      liked: false,
      userId: null,
      mine: false,
      parentId: 'c1',
      rootId: 'c1',
      _seq: 2,
    },
    {
      id: 'c1r2',
      name: '理性看世界',
      seed: 'c1r2',
      content: '希望后续能跟进落地情况。',
      time: '40分钟前',
      createdAt: '2026-10-15T09:35:00',
      likes: 2,
      liked: false,
      userId: null,
      mine: false,
      parentId: 'c1',
      rootId: 'c1',
      _seq: 3,
    },
    {
      id: 'c1r3',
      name: '观察者小张',
      seed: 'c1r3',
      content: '补充一点：第二章的图表引用需要再核对。',
      time: '30分钟前',
      createdAt: '2026-10-15T09:50:00',
      likes: 0,
      liked: false,
      userId: null,
      mine: false,
      parentId: 'c1r1',
      rootId: 'c1',
      _seq: 4,
    },
    {
      id: 'c2',
      name: '沿海居民',
      seed: 'c2',
      content: '我们这边昨天就开始排涝了，预警还是挺及时的，点赞气象台。',
      time: '42分钟前',
      createdAt: '2026-10-15T10:10:00',
      likes: 53,
      liked: true,
      userId: null,
      mine: false,
      parentId: null,
      rootId: null,
      _seq: 5,
    },
    {
      id: 'c3',
      name: '理性看世界',
      seed: 'c3',
      content: '希望落地时能多听听基层声音，别只停留在文件层面。',
      time: '20分钟前',
      createdAt: '2026-10-15T10:30:00',
      likes: 12,
      liked: false,
      userId: null,
      mine: false,
      parentId: null,
      rootId: null,
      _seq: 6,
      // P1-22 演示种子：已被运营软隐藏——公开评论列表实时过滤掉它，
      // 管理端「评论治理」页可见（含理由），可恢复
      hidden: true,
      hiddenAt: '2026-10-15T10:35:00',
      hiddenReason: '演示种子：含广告信息',
    },
    {
      id: 'c4',
      name: '路过的老王',
      seed: 'c4',
      // P1-23 演示种子：命中「博彩」被自动脱敏后**照常发布**，等人工看一眼
      content: '这评论区怎么全是 ** 广告，没人管吗？',
      time: '10分钟前',
      createdAt: '2026-10-15T10:50:00',
      likes: 4,
      liked: false,
      userId: null,
      mine: false,
      parentId: null,
      rootId: null,
      _seq: 7,
      pendingReview: true,
      reviewedAt: null,
    },
  ],
};

/* --------------------- 敏感词审核（P1-23） --------------------- */

/**
 * 后端 `app/moderation.py` 的简化镜像。
 *
 * mock 不追求算法性能（词库几十条，`indexOf` 足够），但**语义必须与后端一致**：
 * 大小写/全半角归一化、空格与标点零宽干扰、白名单区间豁免、昵称命中一律降级
 * 为脱敏、block 不回显命中词。否则前端在 mock 下调通的行为，接真实后端会全变
 * ——这是 mock 最容易埋的坑：看起来能跑，接上真后端才发现开关没生效（#13 同类）。
 */
const MODERATION_ENABLED = String(process.env.MODERATION_ENABLED ?? 'true') !== 'false';
const MODERATION_CHECK_NAME = String(process.env.MODERATION_CHECK_NAME ?? 'true') !== 'false';
const MODERATION_MIN_WORD_LEN = 2;
const MASK_CHAR = '*';
const WORD_ACTIONS = ['block', 'mask', 'allow'];
/** 命中片段的单侧上下文宽度，与后端 SNIPPET_CONTEXT 一致 */
const SNIPPET_CONTEXT = 30;
/** 动作严厉程度：区间重叠取最严（宁可脱敏也别放过） */
const WORD_SEVERITY = { allow: 0, mask: 1, block: 2 };

let wordSeq = 0;
/** 词库：与后端 sensitive_words 同构 */
const words = [
  {
    id: 1,
    word: '加微信',
    action: 'block',
    category: '广告引流',
    enabled: true,
    hitCount: 3,
    createdBy: '13800138000',
    createdAt: '2026-10-15T09:00:00',
    updatedAt: '2026-10-15T09:00:00',
  },
  {
    id: 2,
    word: '博彩',
    action: 'mask',
    category: '违规推广',
    enabled: true,
    hitCount: 12,
    createdBy: '13800138000',
    createdAt: '2026-10-15T09:05:00',
    updatedAt: '2026-10-15T09:05:00',
  },
  {
    id: 3,
    word: '福利',
    action: 'mask',
    category: '违规推广',
    enabled: true,
    hitCount: 7,
    createdBy: '13800138000',
    createdAt: '2026-10-15T09:10:00',
    updatedAt: '2026-10-15T09:10:00',
  },
  // 白名单：单看「福利」会误杀「福利待遇」，把整段加进来即豁免
  {
    id: 4,
    word: '福利待遇',
    action: 'allow',
    category: '白名单',
    enabled: true,
    hitCount: 0,
    createdBy: '13800138000',
    createdAt: '2026-10-15T09:15:00',
    updatedAt: '2026-10-15T09:15:00',
  },
];
wordSeq = words.length;

let hitSeq = 0;
/** 命中日志：与后端 sensitive_hits 同构；snippet 留**原文**（脱敏不可逆） */
const hits = [
  {
    id: 1,
    word: '博彩',
    action: 'mask',
    field: 'content',
    commentId: 'c4',
    newsId: 'n4',
    userId: null,
    userName: '路过的老王',
    snippet: '这评论区怎么全是博彩广告，没人管吗？',
    createdAt: '2026-10-15T10:50:00',
  },
  // 被拦截：没有落库，所以 commentId 为 null——「拦了多少人」不能是黑箱
  {
    id: 2,
    word: '加微信',
    action: 'block',
    field: 'content',
    commentId: null,
    newsId: 'n4',
    userId: null,
    userName: '我',
    snippet: '加微信领资料，先到先得',
    createdAt: '2026-10-15T10:55:00',
  },
  // 昵称命中：降级为脱敏（不阻断发言），但该条正文同时 block 了 → 也没落库
  {
    id: 3,
    word: '博彩',
    action: 'mask',
    field: 'name',
    commentId: null,
    newsId: 'n4',
    userId: null,
    userName: '博彩小妹',
    snippet: '博彩小妹',
    createdAt: '2026-10-15T10:56:00',
  },
];
hitSeq = hits.length;

/**
 * 归一化：逐字符 NFKC（全半角/兼容字符）→ 小写 → 只留字母与数字
 * （丢弃空格、标点、零宽字符、符号、emoji）。
 *
 * 下标映射保留**原文**位置：脱敏要改的是原文，不是归一化后的串。
 */
function modNormalize(text) {
  const src = String(text ?? '');
  const chars = [];
  const indexMap = [];
  let i = 0;
  for (const ch of src) {
    // 逐字符 NFKC：整串 normalize 会改变长度（ﬁ → fi），逐字符才保住下标
    const folded = ch.normalize('NFKC').toLowerCase();
    for (const c of folded) {
      // \p{L} 字母（含 CJK / 假名 / 谚文），\p{N} 数字；其余一律丢弃——
      // 这一步就是「加空格 / 插零宽 / 用标点隔断」绕过的解法
      if (/[\p{L}\p{N}]/u.test(c)) {
        chars.push(c);
        indexMap.push(i);
      }
    }
    i += ch.length;
  }
  return { norm: chars.join(''), indexMap };
}

/** 归一化后剩余的有效字符数（判断「词是不是太短」用这个，不是原文长度） */
const modLength = (text) => modNormalize(text).norm.length;

/** 某类词在原文中的全部区间（原文下标，含首尾） */
function modSpans(raw, action) {
  const { norm, indexMap } = modNormalize(raw);
  const out = [];
  for (const w of words) {
    if (w.enabled !== true || w.action !== action) continue;
    const wn = modNormalize(w.word).norm;
    if (!wn) continue;
    let from = 0;
    for (;;) {
      const at = norm.indexOf(wn, from);
      if (at < 0) break;
      out.push({ word: w.word, start: indexMap[at], end: indexMap[at + wn.length - 1] });
      from = at + 1;
    }
  }
  return out;
}

/**
 * 扫一段文本：返回脱敏后的文本与命中列表。
 *
 * `forceMask` 用于昵称：昵称命中只脱敏，绝不阻断——昵称是账号级属性，
 * 用户没法在评论框里改它，因为昵称而拒绝发言 = 无声禁言。
 */
function modScan(text, field, forceMask = false) {
  const raw = String(text ?? '');
  const empty = { text: raw, hits: [], blocked: false, masked: false };
  if (!raw || !MODERATION_ENABLED) return empty;
  const allow = modSpans(raw, 'allow');
  // 白名单豁免：命中区间被某个 allow 词**完全包裹**才算豁免
  const exempt = (s) => allow.some((a) => a.start <= s.start && s.end <= a.end);
  const spans = [];
  for (const action of ['block', 'mask']) {
    for (const s of modSpans(raw, action)) {
      if (exempt(s)) continue;
      spans.push({ ...s, action });
    }
  }
  if (!spans.length) return empty;

  // 重叠合并：动作取最严、词取最长
  spans.sort((x, y) => x.start - y.start || y.end - x.end);
  const merged = [spans[0]];
  for (const s of spans.slice(1)) {
    const cur = merged[merged.length - 1];
    if (s.start <= cur.end) {
      cur.end = Math.max(cur.end, s.end);
      cur.word = s.word.length > cur.word.length ? s.word : cur.word;
      if (WORD_SEVERITY[s.action] > WORD_SEVERITY[cur.action]) cur.action = s.action;
    } else {
      merged.push(s);
    }
  }
  // 昵称降级：block → mask
  if (forceMask) for (const m of merged) if (m.action === 'block') m.action = 'mask';

  let out = '';
  let at = 0;
  const hitsOut = [];
  for (const m of merged) {
    // 脱敏长度取**词长**而非原文区间长度：否则等于告诉对方"绕过去几个字符"
    out += raw.slice(at, m.start) + MASK_CHAR.repeat(m.word.length);
    at = m.end + 1;
    hitsOut.push({
      word: m.word,
      action: m.action,
      field,
      start: m.start,
      end: m.end,
      snippet: raw.slice(Math.max(0, m.start - SNIPPET_CONTEXT), m.end + 1 + SNIPPET_CONTEXT),
    });
  }
  out += raw.slice(at);
  return {
    text: out,
    hits: hitsOut,
    blocked: !forceMask && merged.some((m) => m.action === 'block'),
    masked: merged.length > 0,
  };
}

/** 一次评论提交的检测：正文 + 昵称 */
function modInspect(content, name) {
  const c = modScan(content, 'content');
  const n = MODERATION_CHECK_NAME ? modScan(name, 'name', true) : { text: name, hits: [] };
  return {
    content: c.text,
    name: n.text,
    hits: [...c.hits, ...n.hits],
    blocked: c.blocked,
    masked: c.masked || n.hits.length > 0,
  };
}

/**
 * 落命中日志 + 累加词命中次数。与后端 `_record_hits` 同口径：
 * 同一 `(字段, 词)` 一次提交只记一行——同一段话三次命中同一个词，对运营是
 * 一件事不是三条事件，重复行会把"哪个词在误杀"淹掉。
 */
function modRecord(list, ctx) {
  const seen = new Set();
  const ts = new Date().toISOString();
  for (const h of list) {
    const key = `${h.field}|${h.word}`;
    if (seen.has(key)) continue;
    seen.add(key);
    hitSeq += 1;
    hits.unshift({
      id: hitSeq,
      word: h.word,
      action: h.action,
      field: h.field,
      commentId: ctx.commentId ?? null,
      newsId: ctx.newsId ?? null,
      userId: null,
      userName: ctx.userName ?? '我',
      snippet: h.snippet,
      createdAt: ts,
    });
    const row = words.find((x) => x.word === h.word);
    if (row) row.hitCount = (row.hitCount ?? 0) + 1;
  }
}

/** 回复 → 输出形态：附「回复 @谁」（父行查找），无嵌套计数 */
function replyOut(r, parents) {
  const p = r.parentId ? parents.get(r.parentId) : null;
  // _seq 仅用于服务端排序；hidden 三件套仅管理端可见（与后端 to_out 一致，
  // 公开输出一概不暴露"它被隐藏了"）——输出前剥掉（浅拷贝 + delete；
  // 解构丢弃会被 lint 拦）
  const out = {
    ...r,
    replyCount: 0,
    replies: [],
    replyToName: p?.name ?? null,
    replyToUserId: p?.userId ?? null,
  };
  delete out._seq;
  delete out.hidden;
  delete out.hiddenAt;
  delete out.hiddenReason;
  return out;
}

/** 主评论 → 输出形态：replyCount + 最早 2 条回复预览（与后端 _decorate_roots 一致） */
function commentOut(root, all) {
  const parents = new Map(all.map((x) => [x.id, x]));
  const replies = all
    .filter((x) => x.rootId === root.id && !x.isDeleted && !x.hidden)
    .sort((x, y) => x._seq - y._seq);
  const out = {
    ...root,
    replyCount: replies.length,
    replies: replies.slice(0, 2).map((r) => replyOut(r, parents)),
  };
  delete out._seq;
  delete out.hidden;
  delete out.hiddenAt;
  delete out.hiddenReason;
  return out;
}

/** token → { phone } */
const tokens = new Map();

/**
 * 收藏 / 浏览历史（按手机号隔离，进程内存储）。
 * 与真实后端一致：服务端为唯一事实源，写操作返回完整集合 { ids, list }。
 */
const marks = new Map();
function marksOf(phone) {
  if (!marks.has(phone)) marks.set(phone, { favorites: [], history: [] });
  return marks.get(phone);
}
/** 历史保留上限（与后端 HISTORY_MAX 对齐） */
const HISTORY_LIMIT = 50;

/** 与后端 app/ranking.py 的 SORT_MODES、前端 constants/sort.ts 三方一致 */
const SORT_MODES = ['latest', 'hot', 'for-you'];

/** 热词统计：term → 次数（进程内，服务重启即清零；仅用于离线演示） */
const terms = new Map();

/* --------------------- 管理端：RSS 源（P1-9） --------------------- */

/** 与后端 app/feeds/sources.py 的 CHANNEL_KEYS 一致（含前端 video 频道） */
const CHANNEL_KEYS = ['recommend', 'hot', 'video', 'tech', 'finance', 'sports', 'car'];

/**
 * mock 侧管理员白名单：与后端 `ADMIN_PHONES` 同理，为空即整体禁用。
 * 这里放演示账号，使 `npm run dev:api` 也能完整体验源管理界面。
 */
const ADMIN_PHONES = ['13800138000'];

/**
 * 应用内白名单（P1-14，与后端 admin_users 表对应）：phone → { note, addedBy, createdAt }。
 * 进程内存储，服务重启即回到初始状态（mock 不持久化，与源配置同理）。
 */
const adminWhitelist = new Map();

/** 双源并集判定（与后端 is_admin 一致）：env ∪ 应用内白名单 */
const isAdminPhone = (phone) =>
  ADMIN_PHONES.includes(String(phone ?? '')) || adminWhitelist.has(String(phone ?? ''));

/** 登录过的手机号（mock 无注册端点，以此推导白名单行的「已注册」状态） */
const seenPhones = new Set();

/**
 * 运营审计（P1-15，与后端 admin_audit_logs 对应）：进程内数组，服务重启即清零。
 * `id` 自增且单调——与后端一致用作游标，避免秒级时间戳同值导致的排序抖动。
 */
const auditLog = [];
let auditSeq = 0;
function audit(actor, action, target, detail) {
  auditLog.push({
    id: ++auditSeq,
    actorPhone: actor,
    action,
    target: target || '',
    detail: detail ?? {},
    createdAt: new Date().toISOString(),
  });
}

/** 内置源（与后端 BUILTIN_SOURCES 同构；进程内可增删改） */
const BUILTIN_SOURCES = [
  {
    key: 'people_politics',
    name: '人民网·时政',
    channel: 'recommend',
    url: 'http://www.people.com.cn/rss/politics.xml',
    weight: 10,
    enabled: true,
  },
  {
    key: 'people_finance',
    name: '人民网·财经',
    channel: 'finance',
    url: 'http://www.people.com.cn/rss/finance.xml',
    weight: 8,
    enabled: true,
  },
  {
    key: 'people_scitech',
    name: '人民网·科技',
    channel: 'tech',
    url: 'http://www.people.com.cn/rss/scitech.xml',
    weight: 8,
    enabled: true,
  },
  {
    key: 'people_sports',
    name: '人民网·体育',
    channel: 'sports',
    url: 'http://www.people.com.cn/rss/sports.xml',
    weight: 6,
    enabled: true,
  },
  {
    key: 'people_society',
    name: '人民网·社会',
    channel: 'hot',
    url: 'http://www.people.com.cn/rss/society.xml',
    weight: 8,
    enabled: true,
  },
  {
    key: 'people_world',
    name: '人民网·国际',
    channel: 'recommend',
    url: 'http://www.people.com.cn/rss/world.xml',
    weight: 8,
    enabled: true,
  },
  {
    key: 'xinhua_politics',
    name: '新华网·时政',
    channel: 'recommend',
    url: 'http://www.xinhuanet.com/politics/news_politics.xml',
    weight: 10,
    enabled: true,
  },
  {
    key: 'xinhua_fortune',
    name: '新华网·财经',
    channel: 'finance',
    url: 'http://www.xinhuanet.com/fortune/news_fortune.xml',
    weight: 8,
    enabled: true,
  },
  {
    key: 'chinanews_scroll',
    name: '中新网·要闻',
    channel: 'hot',
    url: 'https://www.chinanews.com.cn/rss/scroll-news.xml',
    weight: 6,
    enabled: true,
  },
  {
    key: 'chinanews_finance',
    name: '中新网·财经',
    channel: 'finance',
    url: 'https://www.chinanews.com.cn/rss/finance.xml',
    weight: 5,
    enabled: true,
  },
  {
    key: 'chinanews_sports',
    name: '中新网·体育',
    channel: 'sports',
    url: 'https://www.chinanews.com.cn/rss/sports.xml',
    weight: 4,
    enabled: true,
  },
  {
    key: 'aihot_picks',
    name: 'AIHOT·精选',
    channel: 'tech',
    url: 'https://aihot.news/feed.xml',
    weight: 9,
    enabled: true,
  },
  {
    key: 'thepaper',
    name: '澎湃新闻',
    channel: 'recommend',
    url: 'https://feedx.net/rss/thepaper.xml',
    weight: 6,
    enabled: false,
  },
  {
    key: 'zaobao',
    name: '联合早报·中国',
    channel: 'recommend',
    url: 'https://plink.anyfeeder.com/zaobao/realtime/china',
    weight: 5,
    enabled: true,
  },
];

/**
 * 演示用的故障态种子：少量源带失败分级数据，便于在 mock 下看到
 * 「永久性（需人工处理）」与「可重试」两种 UI 分支。key 不存在即按健康兜底。
 *
 * **key 必须在上面的 BUILTIN_SOURCES 里**：早前这里写的是 `sina_tech` / `cnbeta`，
 * 两个都不属于内置源，于是这份种子从来没生效过——mock 下永远看不到那两种分支，
 * 而单测（用的是固定 fixture）照样全绿。改 key 时请回头对照内置源列表。
 */
const SEEDED_FAILURES = {
  chinanews_finance: {
    lastStatus: 503,
    lastError: 'HTTP 503',
    lastItemCount: null,
    consecutiveFailures: 3,
    failureKind: 'transient',
    nextRetryAt: null,
  },
  zaobao: {
    lastStatus: 404,
    lastError: 'HTTP 404',
    lastItemCount: null,
    consecutiveFailures: 7,
    failureKind: 'permanent',
    nextRetryAt: null,
  },
};

/**
 * 演示用的近期健康摘要（P1-19），key 与 SEEDED_FAILURES 对齐。
 *
 * 另加两个**只有历史才看得见**的样本，好让趋势面板真正有东西可讲：
 * - `people_scitech`：成功率 100%，但条数从 30 掉到 4——一直绿着的废源；
 * - `xinhua_fortune`：失败集中在每天凌晨 3 点——对方维护窗口，不是我们的锅。
 */
const SEEDED_HEALTH = {
  chinanews_finance: { attempts: 24, rate: 0.75, avgItems: 18.4 },
  zaobao: { attempts: 24, rate: 0, avgItems: null },
  people_scitech: { attempts: 14, rate: 1, avgItems: 16.1 },
  xinhua_fortune: { attempts: 28, rate: 0.5, avgItems: 22 },
};

/** 列表摘要的窗口天数，与后端 FEED_HEALTH_LIST_DAYS 默认值一致 */
const HEALTH_LIST_DAYS = 7;
/** 健康记录保留期，与后端 FEED_HEALTH_KEEP_DAYS 默认值一致 */
const HEALTH_KEEP_DAYS = 30;

const freshSources = () =>
  BUILTIN_SOURCES.map((s) => ({
    ...s,
    lastStatus: null,
    lastError: null,
    lastItemCount: null,
    lastFetchedAt: null,
    respectRobots: true,
    ...(SEEDED_FAILURES[s.key] ?? {}),
  }));

let sources = freshSources();

/** 输出与后端 admin._out 同构（camelCase，含健康字段） */
function sourceOut(row) {
  return {
    key: row.key,
    name: row.name,
    channel: row.channel,
    url: row.url,
    weight: row.weight,
    enabled: row.enabled !== false,
    lastStatus: row.lastStatus ?? null,
    lastError: row.lastError ?? null,
    lastItemCount: row.lastItemCount ?? null,
    lastFetchedAt: row.lastFetchedAt ?? null,
    respectRobots: row.respectRobots !== false,
    // P1-18：失败分级与熔断（缺省按"没有正在发生的故障"兜底，与前端 toSource 同口径）
    consecutiveFailures: row.consecutiveFailures ?? 0,
    failureKind: row.failureKind ?? null,
    nextRetryAt: row.nextRetryAt ?? null,
    // P1-19：近期健康摘要。没种子的源是 null 而不是全零——
    // "没抓过"和"抓了全挂"在界面上是两种完全不同的状态
    health: SEEDED_HEALTH[row.key] ?? null,
  };
}

/** 两位补零 */
const pad2 = (n) => String(n).padStart(2, '0');

/** 取 `YYYY-MM-DD` */
function dayKey(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/**
 * 演示用的单源健康趋势（P1-19）：按 key 复现四类形态。
 *
 * 刻意让样本覆盖**列表上看不出来**的两种情况（条数下滑、规律抖动），
 * 否则趋势面板在 mock 下只会显示一堆毫无信息量的绿格子。
 */
function mockSourceHealth(row, requestedDays) {
  const win =
    Number(requestedDays) > 0
      ? Math.min(Number(requestedDays), HEALTH_KEEP_DAYS)
      : HEALTH_KEEP_DAYS;
  const base = 20 + ((row.weight ?? 5) % 5) * 3;
  const points = [];
  const failures = [];
  let ok = 0;
  let total = 0;
  let items = 0;
  let lastOkAt = null;
  let lastFailAt = null;

  const push = (i, hour, okFlag, count, err, kind) => {
    const d = new Date();
    d.setDate(d.getDate() - i);
    d.setHours(hour, 0, 0, 0);
    const at = d.toISOString();
    total += 1;
    if (okFlag) {
      ok += 1;
      items += count;
      lastOkAt = at;
    } else {
      lastFailAt = at;
      failures.push({ at, error: err, kind, items: 0 });
    }
  };

  for (let i = win - 1; i >= 0; i -= 1) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const date = dayKey(d);
    const round = { date, total: 0, ok: 0, failed: 0, items: 0 };

    if (row.key === 'zaobao') {
      // 永久性失败：每天两轮都是 404
      for (const hour of [3, 15]) {
        push(i, hour, false, 0, 'HTTP 404', 'permanent');
        round.total += 1;
        round.failed += 1;
      }
    } else if (row.key === 'people_scitech') {
      // 一直成功，但条数从 30 掉到 4：列表上是满分绿，其实已经废了
      const n = Math.max(4, Math.round(30 - ((win - 1 - i) / Math.max(1, win - 1)) * 26));
      push(i, 9, true, n);
      round.total += 1;
      round.ok += 1;
      round.items += n;
    } else if (row.key === 'xinhua_fortune') {
      // 规律抖动：每天凌晨 3 点固定挂一次，下午正常
      push(i, 3, false, 0, 'ReadTimeout: 读取超时', 'transient');
      push(i, 15, true, base);
      round.total += 2;
      round.ok += 1;
      round.failed += 1;
      round.items += base;
    } else if (row.key === 'chinanews_finance') {
      // 可重试失败：四分之一轮次挂 503
      for (const hour of [6, 18]) {
        const bad = (i + hour) % 4 === 0;
        if (bad) {
          push(i, hour, false, 0, 'HTTP 503', 'transient');
          round.total += 1;
          round.failed += 1;
        } else {
          push(i, hour, true, base);
          round.total += 1;
          round.ok += 1;
          round.items += base;
        }
      }
    } else {
      push(i, 9, true, base + (i % 3));
      round.total += 1;
      round.ok += 1;
      round.items += base + (i % 3);
    }
    round.rate = round.total ? Math.round((round.ok / round.total) * 1e4) / 1e4 : null;
    points.push(round);
  }

  return {
    key: row.key,
    days: win,
    keepDays: HEALTH_KEEP_DAYS,
    summary: {
      attempts: total,
      ok,
      failed: total - ok,
      rate: total ? Math.round((ok / total) * 1e4) / 1e4 : null,
      items,
      avgItems: ok ? Math.round((items / ok) * 10) / 10 : null,
      lastOkAt,
      lastFailAt,
    },
    points,
    failures: failures.reverse().slice(0, 20),
  };
}

/** 鉴权 + 管理员校验；失败已直接写响应，返回 null 表示应中断 */
function requireAdmin(req, res) {
  const t = requireAuth(req, res);
  if (!t) return null;
  const phone = tokens.get(t)?.phone;
  if (!isAdminPhone(phone)) {
    fail(res, 403, 40301, '无权限执行该操作');
    return null;
  }
  return phone;
}

/* ----------------------------- 工具 ----------------------------- */

function send(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Allow-Methods': 'GET, POST, PATCH, PUT, DELETE, OPTIONS',
  });
  res.end(body);
}

const ok = (res, data) => send(res, 200, { code: 0, message: 'ok', data });
const fail = (res, status, code, message) => send(res, status, { code, message, data: null });

function readBody(req) {
  return new Promise((resolve) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

/** 解析并校验 Bearer token，返回 token 或 null */
function auth(req) {
  const header = req.headers.authorization || '';
  const m = header.match(/^Bearer\s+(.+)$/i);
  if (m && tokens.has(m[1])) return m[1];
  return null;
}

function requireAuth(req, res) {
  const t = auth(req);
  if (!t) {
    fail(res, 401, 40100, '未登录或登录已过期');
    return null;
  }
  return t;
}

/* ----------------------------- 路由 ----------------------------- */

const server = createServer(async (req, res) => {
  if (req.method === 'OPTIONS') {
    send(res, 204, {});
    return;
  }

  const url = new URL(req.url ?? '/', `http://localhost:${PORT}`);
  const seg = url.pathname
    .replace(/^\/api/, '')
    .split('/')
    .filter(Boolean);
  const [head, a, b, c, d] = seg;
  const method = req.method ?? 'GET';

  // POST /auth/login
  if (head === 'auth' && a === 'login' && method === 'POST') {
    const { phone, password } = await readBody(req);
    if (!/^1\d{10}$/.test(String(phone ?? '')))
      return fail(res, 400, 40001, '请输入正确的 11 位手机号');
    if (String(password ?? '') !== '123456')
      return fail(res, 401, 40101, '手机号或密码错误，请重新输入');
    const token = `tk_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    tokens.set(token, { phone });
    seenPhones.add(String(phone));
    // isAdmin 与后端一致：按白名单判定后随登录响应下发，前端据此展示运营入口
    return ok(res, {
      token,
      user: { name: '明日头条用户', phone, isAdmin: isAdminPhone(phone) },
    });
  }

  // POST /auth/logout
  if (head === 'auth' && a === 'logout' && method === 'POST') {
    const t = requireAuth(req, res);
    if (!t) return;
    tokens.delete(t);
    return ok(res, null);
  }

  // GET /news
  if (head === 'news' && !a && method === 'GET') {
    const channel = url.searchParams.get('channel') || 'recommend';
    const pageSize = Number(url.searchParams.get('pageSize') || 20);
    // 与后端一致：sort 三模式，非法值 422（前端 constants/sort.ts 取值来源）
    const sort = url.searchParams.get('sort') || 'latest';
    if (!SORT_MODES.includes(sort)) {
      return fail(res, 422, 42201, `排序方式不支持：${sort}`);
    }
    let list = channel === 'recommend' ? news : news.filter((n) => n.channel === channel);
    // P1-20：公开列表实时过滤已下架（软下架，与后端 list_news 同口径）
    list = list.filter((n) => n.hidden !== true);
    // 热度模式：无服务端时用评论数近似（离线演示也能看出差异）
    if (sort !== 'latest') {
      list = [...list].sort((x, y) => (y.commentCount ?? 0) - (x.commentCount ?? 0));
    }
    return ok(res, {
      list: list.slice(0, pageSize),
      total: list.length,
      page: Number(url.searchParams.get('page') || 1),
      sort,
    });
  }

  // GET /news/search —— 服务端搜索（必须先于 `/news/:id`，否则 'search' 会被当成 id）
  if (head === 'news' && a === 'search' && !b && method === 'GET') {
    const q = (url.searchParams.get('q') || '').trim();
    if (!q) return ok(res, { list: [], total: 0, page: 1, keyword: '', hasMore: false });
    const kw = q.toLowerCase();
    const hit = news.filter(
      (n) =>
        n.hidden !== true && // P1-20：搜索同样实时过滤已下架
        (String(n.title ?? '')
          .toLowerCase()
          .includes(kw) ||
          String(n.summary ?? '')
            .toLowerCase()
            .includes(kw)),
    );
    // 与后端一致：搜索即计入热词统计
    terms.set(q, (terms.get(q) ?? 0) + 1);
    return ok(res, { list: hit, total: hit.length, page: 1, keyword: q, hasMore: false });
  }

  // GET /news/search/hot —— 热搜词（真实统计，非写死）
  if (head === 'news' && a === 'search' && b === 'hot' && !c && method === 'GET') {
    const limit = Number(url.searchParams.get('limit') || 10);
    const list = [...terms.entries()]
      .sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))
      .slice(0, limit)
      .map(([term, count]) => ({ term, count }));
    return ok(res, { list, days: 7 });
  }

  // GET /news/:id
  if (head === 'news' && a && !b && method === 'GET') {
    const item = news.find((n) => n.id === a);
    // P1-20：已下架与不存在同码 40400（对外不暴露"被下了"）
    if (!item || item.hidden === true) return fail(res, 404, 40400, '内容不存在或已下架');
    return ok(res, item);
  }

  // GET /news/:id/comments（P1-12：只返回主评论，附回复数与最早 2 条预览）
  if (head === 'news' && a && b === 'comments' && !c && method === 'GET') {
    // P1-20：已下架资讯的评论一并不可见（与后端 _get_news_or_404 一致）
    const target = news.find((n) => n.id === a);
    if (!target || target.hidden === true) return fail(res, 404, 40400, '内容不存在或已下架');
    const all = comments[a] ?? [];
    // P1-22：已隐藏的评论对公开侧与已删除同构——实时过滤，无需等定时任务
    const roots = all
      .filter((x) => !x.rootId && !x.isDeleted && !x.hidden)
      .sort((x, y) => y._seq - x._seq);
    // mock 不做真游标：一次返回全部主评论（与既有行为一致）
    return ok(res, {
      list: roots.map((r) => commentOut(r, all)),
      hasMore: false,
      nextCursor: null,
    });
  }

  // GET /news/:id/comments/:cid/replies（时间正序；offset 游标；root 已删返回空）
  if (head === 'news' && a && b === 'comments' && c && d === 'replies' && method === 'GET') {
    const all = comments[a] ?? [];
    const root = all.find((x) => x.id === c);
    // P1-22：root 已软删除或已隐藏 → 回复不可见（失去语境的回复不该继续裸露）
    if (!root || root.isDeleted || root.hidden) {
      return ok(res, { list: [], hasMore: false, nextCursor: null });
    }
    const parents = new Map(all.map((x) => [x.id, x]));
    const replies = all
      .filter((x) => x.rootId === c && !x.isDeleted && !x.hidden)
      .sort((x, y) => x._seq - y._seq);
    const offset = Number(url.searchParams.get('cursor') || 0);
    const limit = Math.min(Number(url.searchParams.get('limit') || 20), 100);
    const page = replies.slice(offset, offset + limit);
    const hasMore = offset + limit < replies.length;
    return ok(res, {
      list: page.map((r) => replyOut(r, parents)),
      hasMore,
      nextCursor: hasMore ? String(offset + limit) : null,
    });
  }

  // POST /news/:id/comments（需登录；parentId 非空时为回复——二级评论）
  if (head === 'news' && a && b === 'comments' && !c && method === 'POST') {
    if (!requireAuth(req, res)) return;
    // P1-20：已下架资讯不能发评论（与后端 _get_news_or_404 一致）
    const target = news.find((n) => n.id === a);
    if (!target || target.hidden === true) return fail(res, 404, 40400, '内容不存在或已下架');
    const { content, parentId } = await readBody(req);
    const text = String(content ?? '').trim();
    if (!text) return fail(res, 400, 40002, '评论内容不能为空');

    const all = comments[a] ?? [];
    let parent = null;
    if (parentId) {
      parent = all.find((x) => x.id === parentId);
      // 与后端一致：被回复评论必须存在、未删除、未隐藏（同文章由路径保证）。
      // 隐藏与被删除对回复者同构：都该像"不存在"一样拒绝
      if (!parent || parent.isDeleted || parent.hidden) {
        return fail(res, 400, 40003, '回复的评论不存在');
      }
    }
    // P1-23：落库**之前**检测（唯一写入口，主评论与回复都走这里）
    const cid = `c_${Date.now()}`;
    const inspection = modInspect(text, '我');
    if (inspection.hits.length) {
      modRecord(inspection.hits, {
        // 拦截时不落库 → 没有评论 id；但命中日志照样写（拦截也要留痕）
        commentId: inspection.blocked ? null : cid,
        newsId: a,
        userName: '我',
      });
      if (inspection.blocked) {
        // 提示不回显命中词：回显等于给绕过者一份"哪些词不能写"的对照表
        return fail(res, 400, 40005, '评论包含不当内容，请修改后重试');
      }
    }
    const created = {
      id: cid,
      name: inspection.name || '我',
      seed: 'me',
      content: inspection.content,
      // 脱敏过就进待复核：机器判断是概率性的，让人看一眼比直接吞掉便宜
      pendingReview: inspection.masked,
      reviewedAt: inspection.masked ? null : undefined,
      time: '刚刚',
      // 与后端 create_comment 一致：created_at 是绝对 ISO 时间（排序唯一依据），
      // time 只是展示文案。管理端评论治理列表按它倒序。
      createdAt: new Date().toISOString(),
      likes: 0,
      liked: false,
      // 归属字段：与后端 to_out 一致（自己的评论 mine=true）
      userId: null,
      mine: true,
      parentId: parent?.id ?? null,
      // 回复的回复拍平到同一 root（与后端 create_comment 一致）
      rootId: parent ? (parent.rootId ?? parent.id) : null,
      _seq: ++commentSeq,
    };
    comments[a] = [created, ...all];
    const out = {
      ...created,
      replyToName: parent?.name ?? null,
      replyToUserId: parent?.userId ?? null,
    };
    delete out._seq;
    return ok(res, out);
  }

  // PATCH /news/:id/comments/:cid（需登录；点赞幂等；主评论与回复通用）
  if (head === 'news' && a && b === 'comments' && c && method === 'PATCH') {
    if (!requireAuth(req, res)) return;
    const { liked } = await readBody(req);
    const list = comments[a] ?? [];
    const target = list.find((x) => x.id === c);
    // P1-22：已隐藏的评论不可再点赞（避免为不可见内容累加计数、点赞列表出幽灵）
    if (!target || target.isDeleted || target.hidden) return fail(res, 404, 40401, '评论不存在');
    const next = Boolean(liked);
    if (target.liked !== next) {
      target.liked = next;
      target.likes = Math.max(target.likes + (next ? 1 : -1), 0);
    }
    const parents = new Map(list.map((x) => [x.id, x]));
    return ok(res, target.rootId ? replyOut(target, parents) : commentOut(target, list));
  }

  // DELETE /news/:id/comments/:cid（需登录；软删除；mock 无真实用户，mine 即作者）
  if (head === 'news' && a && b === 'comments' && c && method === 'DELETE') {
    if (!requireAuth(req, res)) return;
    const list = comments[a] ?? [];
    const target = list.find((x) => x.id === c);
    if (!target || target.isDeleted) return fail(res, 404, 40401, '评论不存在');
    if (!target.mine) return fail(res, 403, 40302, '只能删除自己发表的评论');
    target.isDeleted = true;
    target.deletedAt = new Date().toISOString();
    const parents = new Map(list.map((x) => [x.id, x]));
    return ok(res, target.rootId ? replyOut(target, parents) : commentOut(target, list));
  }

  // ---- 收藏 / 浏览历史（/me/*，需登录）----
  // 与真实后端契约一致：写操作返回完整集合 { ids, list }；PUT 为并集合并（只增不删）。
  if (head === 'me' && (a === 'favorites' || a === 'history')) {
    const t = requireAuth(req, res);
    if (!t) return;
    const store = marksOf(tokens.get(t)?.phone ?? 'unknown');
    const listKey = a;
    const payload = () => ({
      ids: [...store[listKey]],
      list: store[listKey].map((id) => news.find((n) => n.id === id)).filter(Boolean),
    });
    const merge = (ids) => {
      for (const id of ids) {
        if (!news.some((n) => n.id === id) || store[listKey].includes(id)) continue;
        store[listKey].unshift(id);
      }
      if (listKey === 'history') store[listKey] = store[listKey].slice(0, HISTORY_LIMIT);
    };

    if (method === 'GET') return ok(res, payload());

    if (method === 'POST') {
      const { newsId } = await readBody(req);
      const id = String(newsId ?? '');
      // 收藏：资讯不存在返回 404；历史：被动副作用，静默忽略（与后端一致）
      if (!news.some((n) => n.id === id)) {
        if (listKey === 'favorites') return fail(res, 404, 40400, '内容不存在或已下架');
        return ok(res, payload());
      }
      if (store[listKey].includes(id)) {
        // 收藏：幂等，且不改变既有顺序（与后端 created_at 语义一致）
        if (listKey === 'favorites') return ok(res, payload());
        // 历史：重复浏览刷新到最前
        store[listKey] = store[listKey].filter((x) => x !== id);
      }
      store[listKey].unshift(id);
      if (listKey === 'history') store[listKey] = store[listKey].slice(0, HISTORY_LIMIT);
      return ok(res, payload());
    }

    if (method === 'DELETE') {
      if (b)
        store[listKey] = store[listKey].filter((x) => x !== b); // 取消单条
      else store[listKey] = []; // 清空
      return ok(res, payload());
    }

    if (method === 'PUT') {
      const { ids } = await readBody(req);
      merge(Array.isArray(ids) ? ids.map(String) : []);
      return ok(res, payload());
    }
  }

  /* --------------------- /admin/sources --------------------- */

  // POST /admin/sources/reset
  if (head === 'admin' && a === 'sources' && b === 'reset' && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    sources = freshSources();
    audit(operator, 'source.reset', '', { seeded: sources.length });
    return ok(res, { seeded: sources.length });
  }

  // GET /admin/sources
  if (head === 'admin' && a === 'sources' && !b && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const list = [...sources].sort((x, y) => y.weight - x.weight || x.key.localeCompare(y.key));
    // healthDays 随列表下发：成功率不带时间范围就是个无法解读的数字
    return ok(res, {
      list: list.map(sourceOut),
      total: list.length,
      healthDays: HEALTH_LIST_DAYS,
    });
  }

  // GET /admin/sources/:key/health —— 单源健康趋势（P1-19）
  if (head === 'admin' && a === 'sources' && b && c === 'health' && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const row = sources.find((s) => s.key === b);
    if (!row) return fail(res, 404, 40402, '源不存在');
    // days 校验与真实后端 Query(ge=1, le=365) 对齐：非整数 / 0 / 越界一律 422。
    // 不能让 days=0 在 mock 上静默回落成保留期——两个后端口径不一致时，
    // 契约检查会在一个后端绿、另一个后端红，排查半天以为是回归
    const raw = url.searchParams.get('days');
    const bad = raw !== null && (!/^\d+$/.test(raw) || Number(raw) < 1 || Number(raw) > 365);
    if (bad) return fail(res, 422, 42200, '参数校验失败：days 须为 1~365 的整数');
    const days = raw === null ? 0 : Number(raw);
    return ok(res, mockSourceHealth(row, days));
  }

  // POST /admin/sources
  if (head === 'admin' && a === 'sources' && !b && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const body = await readBody(req);
    const { key, name, channel, url } = body;
    if (!/^[a-z0-9_]+$/.test(String(key ?? ''))) {
      return fail(res, 422, 42200, '参数校验失败：key 标识只能用英文小写字母、数字和下划线');
    }
    if (!CHANNEL_KEYS.includes(channel)) {
      return fail(
        res,
        422,
        42200,
        `参数校验失败：channel 必须是以下之一：${CHANNEL_KEYS.join(', ')}`,
      );
    }
    if (!/^https?:\/\/\S+$/i.test(String(url ?? ''))) {
      return fail(res, 400, 40004, '源地址必须为 http/https URL');
    }
    if (sources.some((s) => s.key === key)) {
      return fail(res, 409, 40902, '该源标识已存在');
    }
    const row = {
      key,
      name: String(name ?? '').trim() || key,
      channel,
      url: String(url).trim(),
      weight: Number.isFinite(Number(body.weight)) ? Number(body.weight) : 5,
      enabled: body.enabled !== false,
      respectRobots: body.respectRobots !== false,
      lastStatus: null,
      lastError: null,
      lastItemCount: null,
      lastFetchedAt: null,
    };
    sources.push(row);
    audit(operator, 'source.create', key, { snapshot: sourceOut(row) });
    return ok(res, sourceOut(row));
  }

  // PATCH /admin/sources/:key
  if (head === 'admin' && a === 'sources' && b && method === 'PATCH') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const row = sources.find((s) => s.key === b);
    if (!row) return fail(res, 404, 40402, '源不存在');
    const body = await readBody(req);
    if (body.channel !== undefined && !CHANNEL_KEYS.includes(body.channel)) {
      return fail(
        res,
        422,
        42200,
        `参数校验失败：channel 必须是以下之一：${CHANNEL_KEYS.join(', ')}`,
      );
    }
    if (body.url !== undefined && !/^https?:\/\/\S+$/i.test(String(body.url))) {
      return fail(res, 400, 40004, '源地址必须为 http/https URL');
    }
    const before = sourceOut(row);
    for (const f of ['name', 'channel', 'url', 'weight', 'enabled', 'respectRobots']) {
      if (body[f] !== undefined) row[f] = body[f];
    }
    const after = sourceOut(row);
    // 与后端一致：只记真正变化的字段，无变化的 PATCH 不留痕
    const changes = {};
    for (const f of ['name', 'channel', 'url', 'weight', 'enabled', 'respectRobots']) {
      if (before[f] !== after[f]) changes[f] = [before[f], after[f]];
    }
    if (Object.keys(changes).length) audit(operator, 'source.update', b, { changes });
    return ok(res, sourceOut(row));
  }

  // DELETE /admin/sources/:key
  if (head === 'admin' && a === 'sources' && b && method === 'DELETE') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const i = sources.findIndex((s) => s.key === b);
    if (i < 0) return fail(res, 404, 40402, '源不存在');
    // 快照必须在 splice 之前取：删完就没了
    audit(operator, 'source.delete', b, { snapshot: sourceOut(sources[i]) });
    const [row] = sources.splice(i, 1);
    return ok(res, { key: row.key });
  }

  /* --------------------- 管理端：资讯审核与下架（P1-20） --------------------- */

  /** status 三态，与后端 Query(pattern=...) 一致 */
  const NEWS_STATUSES = ['all', 'visible', 'hidden'];

  /** 管理端资讯行输出，与后端 admin._news_out 同构（公开字段的超集 + 下架三件套） */
  function adminNewsOut(row) {
    return {
      id: row.id,
      title: row.title,
      summary: row.summary ?? null,
      source: row.source ?? '',
      commentCount: row.commentCount ?? 0,
      time: row.time ?? '',
      images: row.images ?? null,
      cover: row.cover ?? null,
      videoUrl: row.videoUrl ?? null,
      playCount: row.playCount ?? null,
      isTop: row.isTop === true,
      channel: row.channel ?? 'recommend',
      variant: row.variant ?? 'left-right',
      publishedAt: row.publishedAt ?? null,
      hotScore: Number.isFinite(Number(row.hotScore)) ? Number(row.hotScore) : 0,
      hidden: row.hidden === true,
      hiddenAt: row.hiddenAt ?? null,
      hiddenReason: row.hiddenReason ?? null,
    };
  }

  // GET /admin/news
  if (head === 'admin' && a === 'news' && !b && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const q = url.searchParams;
    const status = q.get('status') || 'all';
    if (!NEWS_STATUSES.includes(status)) {
      return fail(res, 422, 42200, `参数校验失败：status 必须是 ${NEWS_STATUSES.join('/')} 之一`);
    }
    const channel = q.get('channel');
    if (channel !== null && !CHANNEL_KEYS.includes(channel)) {
      return fail(
        res,
        422,
        42201,
        `参数校验失败：channel 必须是以下之一：${CHANNEL_KEYS.join(', ')}`,
      );
    }
    const keyword = q.get('keyword') ?? '';
    if (keyword.length > 50) return fail(res, 422, 42200, '参数校验失败：keyword 不能超过 50 字');
    const limit = Math.min(Math.max(Number(q.get('limit') ?? 20) || 20, 1), 100);
    const kw = keyword.trim().toLowerCase();
    let rows = news.filter((n) =>
      status === 'all' ? true : status === 'hidden' ? n.hidden === true : n.hidden !== true,
    );
    if (kw) {
      rows = rows.filter(
        (n) =>
          String(n.title ?? '')
            .toLowerCase()
            .includes(kw) ||
          String(n.summary ?? '')
            .toLowerCase()
            .includes(kw),
      );
    }
    if (channel) rows = rows.filter((n) => n.channel === channel);
    // 排序与后端一致：published_at DESC, id DESC（管理端要时间线，不搞置顶优先）
    rows = [...rows].sort(
      (x, y) =>
        String(y.publishedAt ?? '').localeCompare(String(x.publishedAt ?? '')) ||
        String(y.id).localeCompare(String(x.id)),
    );
    const total = rows.length;
    const cursor = q.get('cursor');
    if (cursor) {
      let pub = '';
      let nid = '';
      try {
        const decoded = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8'));
        pub = String(decoded?.[0] ?? '');
        nid = String(decoded?.[1] ?? '');
      } catch {
        return fail(res, 400, 40003, '分页游标无效');
      }
      rows = rows.filter(
        (n) =>
          String(n.publishedAt ?? '') < pub ||
          (String(n.publishedAt ?? '') === pub && String(n.id) < nid),
      );
    }
    const page = rows.slice(0, limit);
    const hasMore = rows.length > limit;
    const last = page.at(-1);
    return ok(res, {
      list: page.map(adminNewsOut),
      total,
      hasMore,
      nextCursor:
        hasMore && last
          ? Buffer.from(JSON.stringify([last.publishedAt ?? '', last.id])).toString('base64url')
          : null,
    });
  }

  // POST /admin/news/:id/hide（软下架，幂等；理由必填）
  // 路径分段：/admin/news/:id/hide → b 是资讯 id，c 才是动作
  if (head === 'admin' && a === 'news' && b && c === 'hide' && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const item = news.find((n) => n.id === b);
    if (!item) return fail(res, 404, 40404, '资讯不存在');
    const body = await readBody(req);
    const reason = String(body.reason ?? '').trim();
    // 与后端 NewsHideIn 同口径：空/纯空白 422（留痕是准入条件）
    if (!reason) return fail(res, 422, 42200, '参数校验失败：下架理由不能为空');
    if (reason.length > 300) return fail(res, 422, 42200, '参数校验失败：下架理由不能超过 300 字');
    // 幂等：已下架不覆盖原理由、不重复记审计
    if (item.hidden !== true) {
      item.hidden = true;
      item.hiddenAt = new Date().toISOString();
      item.hiddenReason = reason;
      // detail 带标题：审计页的 target 只有 id，运营认标题不认 id
      audit(operator, 'news.hide', item.id, { reason, title: item.title });
    }
    return ok(res, adminNewsOut(item), '已下架');
  }

  // POST /admin/news/:id/restore（恢复，幂等；理由只对下架状态有意义，恢复即清空）
  if (head === 'admin' && a === 'news' && b && c === 'restore' && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const item = news.find((n) => n.id === b);
    if (!item) return fail(res, 404, 40404, '资讯不存在');
    if (item.hidden === true) {
      item.hidden = false;
      item.hiddenAt = null;
      item.hiddenReason = null;
      audit(operator, 'news.restore', item.id, { title: item.title });
    }
    return ok(res, adminNewsOut(item), '已恢复');
  }

  // PATCH /admin/news/:id（局部更新，当前仅置顶；字段级 diff，无变化不记审计）
  if (head === 'admin' && a === 'news' && b && !c && method === 'PATCH') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const item = news.find((n) => n.id === b);
    if (!item) return fail(res, 404, 40404, '资讯不存在');
    const body = await readBody(req);
    const before = adminNewsOut(item);
    // 多词字段走驼峰：蛇形 is_top 会被静默忽略（与后端 pydantic extra='ignore' 一致）
    if (body.isTop !== undefined) item.isTop = body.isTop === true;
    const after = adminNewsOut(item);
    const changes = {};
    if (before.isTop !== after.isTop) changes.isTop = [before.isTop, after.isTop];
    if (Object.keys(changes).length) {
      audit(operator, 'news.update', item.id, { changes, title: item.title });
    }
    return ok(res, after, '已更新');
  }

  // GET /admin/news/:id（40404 与公开端 40400 刻意分开）
  if (head === 'admin' && a === 'news' && b && !c && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const item = news.find((n) => n.id === b);
    if (!item) return fail(res, 404, 40404, '资讯不存在');
    return ok(res, adminNewsOut(item));
  }

  /* --------------------- 管理端：评论治理（P1-22） --------------------- */

  /** status 四态，与后端 Query(pattern=...) 一致（pending 为 P1-23 新增） */
  const COMMENT_STATUSES = ['all', 'visible', 'hidden', 'pending'];

  /** 跨文章找一条评论：评论按文章分桶存，治理端要按 id 定位 → { row, newsId } */
  function findComment(id) {
    for (const [newsId, list] of Object.entries(comments)) {
      const row = list.find((x) => x.id === id);
      if (row) return { row, newsId };
    }
    return null;
  }

  /** 全部评论（含已隐藏，不含已软删除）的 { row, newsId } 平表 */
  function allCommentPairs() {
    const pairs = [];
    for (const [newsId, list] of Object.entries(comments)) {
      for (const row of list) pairs.push({ row, newsId });
    }
    return pairs;
  }

  /**
   * 管理端评论行输出，与后端 admin._comment_out 同构（公开字段的超集 +
   * 隐藏三件套 + 所属资讯标题 + 作者名）。replyCount 只数**当前可见**回复。
   */
  function adminCommentOut(row, newsId) {
    const siblings = comments[newsId] ?? [];
    return {
      id: row.id,
      newsId,
      newsTitle: news.find((n) => n.id === newsId)?.title ?? '',
      userId: row.userId ?? null,
      userName: row.name ?? '匿名',
      content: row.content ?? '',
      createdAt: row.createdAt ?? '',
      likes: row.likes ?? 0,
      parentId: row.parentId ?? null,
      rootId: row.rootId ?? null,
      replyCount: row.rootId
        ? 0
        : siblings.filter((x) => x.rootId === row.id && !x.isDeleted && !x.hidden).length,
      isDeleted: row.isDeleted === true,
      hidden: row.hidden === true,
      hiddenAt: row.hiddenAt ?? null,
      hiddenReason: row.hiddenReason ?? null,
      // P1-23：与 hidden 正交——hidden 是已处置不可见，pending 是未处置但可见
      pendingReview: row.pendingReview === true,
      reviewedAt: row.reviewedAt ?? null,
    };
  }

  // GET /admin/comments（筛选 + 搜索 + 游标分页；游标为上一页末条 id）
  if (head === 'admin' && a === 'comments' && !b && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const q = url.searchParams;
    const status = q.get('status') || 'all';
    if (!COMMENT_STATUSES.includes(status)) {
      return fail(
        res,
        422,
        42200,
        `参数校验失败：status 必须是 ${COMMENT_STATUSES.join('/')} 之一`,
      );
    }
    const keyword = q.get('keyword') ?? '';
    if (keyword.length > 50) return fail(res, 422, 42200, '参数校验失败：keyword 不能超过 50 字');
    const newsId = q.get('newsId');
    const limit = Math.min(Math.max(Number(q.get('limit') ?? 20) || 20, 1), 100);
    // 与后端同口径：已软删除一概不返回（作者侧不可逆行为，治理视图同样不存在）
    let pairs = allCommentPairs().filter((p) => p.row.isDeleted !== true);
    if (status === 'visible') pairs = pairs.filter((p) => p.row.hidden !== true);
    else if (status === 'hidden') pairs = pairs.filter((p) => p.row.hidden === true);
    else if (status === 'pending') pairs = pairs.filter((p) => p.row.pendingReview === true);
    if (newsId) pairs = pairs.filter((p) => p.newsId === newsId);
    const kw = keyword.trim().toLowerCase();
    if (kw) {
      // 与后端 _escape_like 同口径：% / _ 按字面量匹配（不转义时搜一个百分号
      // 等于"匹配全部"）——正则整体转义即可
      const esc = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp(esc);
      pairs = pairs.filter((p) => re.test(String(p.row.content ?? '').toLowerCase()));
    }
    const total = pairs.length;
    // 排序与后端一致：created_at DESC, id DESC（mock 的 _seq 即创建序，同序）
    pairs = [...pairs].sort(
      (x, y) =>
        String(y.row.createdAt ?? '').localeCompare(String(x.row.createdAt ?? '')) ||
        y.row._seq - x.row._seq,
    );
    const cursor = q.get('cursor');
    if (cursor) {
      const anchor = findComment(cursor);
      // 与后端一致：游标锚点找不到时**不报错**，仅忽略分页条件
      if (anchor) pairs = pairs.filter((p) => p.row._seq < anchor.row._seq);
    }
    const page = pairs.slice(0, limit);
    const hasMore = pairs.length > limit;
    const last = page.at(-1);
    return ok(res, {
      list: page.map((p) => adminCommentOut(p.row, p.newsId)),
      total,
      hasMore,
      nextCursor: hasMore && last ? last.row.id : null,
    });
  }

  // POST /admin/comments/:id/hide（软隐藏，幂等；理由必填）
  // 路径分段：/admin/comments/:id/hide → b 是评论 id，c 才是动作
  if (head === 'admin' && a === 'comments' && b && c === 'hide' && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const found = findComment(b);
    // 40405：已软删除对治理视图同样不存在（与后端 _comment_not_found 一致）
    if (!found || found.row.isDeleted) return fail(res, 404, 40405, '评论不存在或已删除');
    const body = await readBody(req);
    const reason = String(body.reason ?? '').trim();
    // 与后端 CommentHideIn 同口径：空/纯空白 422（留痕是准入条件）
    if (!reason) return fail(res, 422, 42200, '参数校验失败：隐藏理由不能为空');
    if (reason.length > 300) return fail(res, 422, 42200, '参数校验失败：隐藏理由不能超过 300 字');
    // 幂等：已隐藏不覆盖原理由、不重复记审计
    if (found.row.hidden !== true) {
      found.row.hidden = true;
      found.row.hiddenAt = new Date().toISOString();
      found.row.hiddenReason = reason;
      const item = news.find((n) => n.id === found.newsId);
      if (item) {
        // 隐藏即从公开视图消失：计数回退（下限 0，防历史脏数据算成负数）
        item.commentCount = Math.max((item.commentCount ?? 0) - 1, 0);
      }
      const title = item?.title ?? '';
      // 评论没有标题，审计时间线认内容片段 + 资讯标题 + 作者名（运营认内容不认 id）
      audit(operator, 'comment.hide', found.row.id, {
        reason,
        content: String(found.row.content ?? '').slice(0, 50),
        newsTitle: title,
        userName: found.row.name ?? '匿名',
      });
    }
    return ok(res, adminCommentOut(found.row, found.newsId), '已隐藏');
  }

  // POST /admin/comments/:id/restore（恢复，幂等；理由只对隐藏状态有意义，恢复即清空）
  if (head === 'admin' && a === 'comments' && b && c === 'restore' && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const found = findComment(b);
    if (!found || found.row.isDeleted) return fail(res, 404, 40405, '评论不存在或已删除');
    if (found.row.hidden === true) {
      found.row.hidden = false;
      found.row.hiddenAt = null;
      found.row.hiddenReason = null;
      const item = news.find((n) => n.id === found.newsId);
      if (item) item.commentCount = (item.commentCount ?? 0) + 1;
      audit(operator, 'comment.restore', found.row.id, {
        content: String(found.row.content ?? '').slice(0, 50),
        newsTitle: item?.title ?? '',
        userName: found.row.name ?? '匿名',
      });
    }
    return ok(res, adminCommentOut(found.row, found.newsId), '已恢复');
  }

  // POST /admin/comments/:id/review（通过复核，幂等：只清待复核标记）
  if (head === 'admin' && a === 'comments' && b && c === 'review' && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const found = findComment(b);
    if (!found || found.row.isDeleted) return fail(res, 404, 40405, '评论不存在或已删除');
    if (found.row.pendingReview === true) {
      found.row.pendingReview = false;
      found.row.reviewedAt = new Date().toISOString();
      const item = news.find((n) => n.id === found.newsId);
      audit(operator, 'comment.review', found.row.id, {
        content: String(found.row.content ?? '').slice(0, 50),
        newsTitle: item?.title ?? '',
        userName: found.row.name ?? '匿名',
      });
    }
    return ok(res, adminCommentOut(found.row, found.newsId), '已通过复核');
  }

  // GET /admin/comments/:id（40405 与公开端 40401 刻意分开）
  if (head === 'admin' && a === 'comments' && b && !c && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const found = findComment(b);
    if (!found || found.row.isDeleted) return fail(res, 404, 40405, '评论不存在或已删除');
    return ok(res, adminCommentOut(found.row, found.newsId));
  }

  /* --------------------- 管理端：敏感词库与命中日志（P1-23） --------------------- */

  const wordOut = (w) => ({ ...w });

  // GET /admin/words（按命中次数降序：高频词要么是热点，要么是误杀大户）
  if (head === 'admin' && a === 'words' && !b && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const q = url.searchParams;
    const kw = (q.get('keyword') ?? '').trim().toLowerCase();
    const action = q.get('action') ?? '';
    if (action && !['block', 'mask', 'allow'].includes(action)) {
      return fail(res, 422, 42200, '参数校验失败：action 必须是 block/mask/allow 之一');
    }
    const enabledRaw = q.get('enabled');
    const limit = Math.min(Math.max(Number(q.get('limit') ?? 200) || 200, 1), 500);
    const offset = Math.max(Number(q.get('offset') ?? 0) || 0, 0);
    let rows = [...words];
    if (kw) rows = rows.filter((w) => w.word.toLowerCase().includes(kw));
    if (action) rows = rows.filter((w) => w.action === action);
    if (enabledRaw !== null) rows = rows.filter((w) => w.enabled === (enabledRaw === 'true'));
    rows.sort((x, y) => y.hitCount - x.hitCount || y.id - x.id);
    return ok(res, { list: rows.slice(offset, offset + limit).map(wordOut), total: rows.length });
  }

  // POST /admin/words（新增：太短 42202 / 重复 40905；保存即生效）
  if (head === 'admin' && a === 'words' && !b && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const body = await readBody(req);
    const word = String(body.word ?? '').trim();
    const action = String(body.action ?? 'mask');
    if (!WORD_ACTIONS.includes(action)) {
      return fail(res, 422, 42200, '参数校验失败：action 必须是 block/mask/allow 之一');
    }
    // 与后端同口径：按**归一化后**的有效字符数判断（「傻 逼」算 2，「傻!」算 1）
    if (modLength(word) < MODERATION_MIN_WORD_LEN) {
      return fail(
        res,
        422,
        42202,
        `敏感词至少需 ${MODERATION_MIN_WORD_LEN} 个有效字符（空格与标点不计）`,
      );
    }
    if (words.some((w) => w.word === word)) {
      return fail(res, 409, 40905, '该敏感词已存在');
    }
    const ts = new Date().toISOString();
    wordSeq += 1;
    const row = {
      id: wordSeq,
      word,
      action,
      category: String(body.category ?? '')
        .trim()
        .slice(0, 50),
      enabled: body.enabled !== false,
      hitCount: 0,
      createdBy: operator,
      createdAt: ts,
      updatedAt: ts,
    };
    words.push(row);
    audit(operator, 'word.create', word, {
      action,
      category: row.category,
      enabled: row.enabled,
    });
    return ok(res, wordOut(row), '已添加');
  }

  // PATCH /admin/words/:id（**不许改词本身**：改词等于删一条加一条）
  if (head === 'admin' && a === 'words' && b && !c && method === 'PATCH') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const row = words.find((w) => String(w.id) === String(b));
    if (!row) return fail(res, 404, 40406, '敏感词不存在');
    const body = await readBody(req);
    const changes = {};
    if (body.action !== undefined && body.action !== row.action) {
      if (!WORD_ACTIONS.includes(String(body.action))) {
        return fail(res, 422, 42200, '参数校验失败：action 必须是 block/mask/allow 之一');
      }
      changes.action = [row.action, body.action];
      row.action = body.action;
    }
    if (body.category !== undefined && body.category !== row.category) {
      changes.category = [
        row.category,
        String(body.category ?? '')
          .trim()
          .slice(0, 50),
      ];
      row.category = changes.category[1];
    }
    const nextEnabled = body.enabled === undefined ? undefined : body.enabled !== false;
    if (nextEnabled !== undefined && nextEnabled !== row.enabled) {
      changes.enabled = [row.enabled, nextEnabled];
      row.enabled = nextEnabled;
    }
    // 字段级 diff：无变化不记审计（与资讯置顶同款，"点了一下"不等于"改了"）
    if (!Object.keys(changes).length) return ok(res, wordOut(row));
    row.updatedAt = new Date().toISOString();
    audit(operator, 'word.update', row.word, { changes });
    return ok(res, wordOut(row), '已更新');
  }

  // DELETE /admin/words/:id（审计留快照：删错了要能知道原来是什么）
  if (head === 'admin' && a === 'words' && b && !c && method === 'DELETE') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const i = words.findIndex((w) => String(w.id) === String(b));
    if (i < 0) return fail(res, 404, 40406, '敏感词不存在');
    const snapshot = wordOut(words[i]);
    audit(operator, 'word.delete', snapshot.word, { snapshot });
    words.splice(i, 1);
    return ok(res, snapshot, '已删除');
  }

  // POST /admin/words/reload（重建索引：改词本进程已即时生效，这是给其他实例用的）
  if (head === 'admin' && a === 'words' && b === 'reload' && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const count = words.filter((w) => w.enabled === true).length;
    audit(operator, 'word.reload', '', { count });
    return ok(res, { count }, '词库已重载');
  }

  // GET /admin/moderation/hits（只读；价值在回答误杀，不在"看出事了"）
  if (head === 'admin' && a === 'moderation' && b === 'hits' && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const q = url.searchParams;
    const word = (q.get('word') ?? '').trim();
    const action = q.get('action') ?? '';
    const field = q.get('field') ?? '';
    if (action && !['block', 'mask'].includes(action)) {
      return fail(res, 422, 42200, '参数校验失败：action 必须是 block/mask 之一');
    }
    if (field && !['content', 'name'].includes(field)) {
      return fail(res, 422, 42200, '参数校验失败：field 必须是 content/name 之一');
    }
    const limit = Math.min(Math.max(Number(q.get('limit') ?? 20) || 20, 1), 100);
    const cursor = q.get('cursor');
    let rows = [...hits];
    if (word) rows = rows.filter((h) => h.word === word);
    if (action) rows = rows.filter((h) => h.action === action);
    if (field) rows = rows.filter((h) => h.field === field);
    const total = rows.length;
    rows.sort((x, y) => y.id - x.id); // 新的在前
    if (cursor) {
      if (!/^\d+$/.test(cursor)) return fail(res, 400, 40003, '分页游标无效');
      rows = rows.filter((h) => h.id < Number(cursor));
    }
    const hasMore = rows.length > limit;
    const page = rows.slice(0, limit);
    return ok(res, {
      list: page.map((h) => ({ ...h })),
      total,
      hasMore,
      nextCursor: hasMore ? page[page.length - 1].id : null,
    });
  }

  /* --------------------- 管理端：管理员白名单（P1-14） --------------------- */

  const adminUserOut = (phone, row) => ({
    phone,
    note: row.note || '',
    addedBy: row.addedBy || '',
    createdAt: row.createdAt,
    registered: seenPhones.has(phone),
    name: seenPhones.has(phone) ? '明日头条用户' : '',
  });

  // GET /admin/users
  if (head === 'admin' && a === 'users' && !b && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const list = [...adminWhitelist.entries()]
      .sort((x, y) => String(x[1].createdAt).localeCompare(String(y[1].createdAt)))
      .map(([phone, row]) => adminUserOut(phone, row));
    return ok(res, { list, total: list.length, envAdminCount: ADMIN_PHONES.length });
  }

  // POST /admin/users
  if (head === 'admin' && a === 'users' && !b && method === 'POST') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    const body = await readBody(req);
    const phone = String(body.phone ?? '');
    if (!/^1\d{10}$/.test(phone)) {
      return fail(res, 422, 42200, '参数校验失败：phone 需为 11 位手机号（1 开头）');
    }
    if (adminWhitelist.has(phone) || ADMIN_PHONES.includes(phone)) {
      return fail(res, 409, 40903, '该手机号已是管理员');
    }
    const row = {
      note: String(body.note ?? '')
        .trim()
        .slice(0, 100),
      addedBy: operator,
      createdAt: new Date().toISOString(),
    };
    adminWhitelist.set(phone, row);
    audit(operator, 'admin.add', phone, { note: row.note || '' });
    return ok(res, adminUserOut(phone, row), '已添加管理员');
  }

  // DELETE /admin/users/:phone
  if (head === 'admin' && a === 'users' && b && method === 'DELETE') {
    const operator = requireAdmin(req, res);
    if (!operator) return;
    if (!adminWhitelist.has(b)) return fail(res, 404, 40403, '管理员不存在');
    // 末位保护与后端一致：env 为空且库中仅剩这一条时拒绝
    if (adminWhitelist.size - 1 <= 0 && ADMIN_PHONES.length === 0) {
      return fail(res, 409, 40904, '已是最后一位管理员，不能移除');
    }
    audit(operator, 'admin.remove', b, { note: adminWhitelist.get(b)?.note || '' });
    adminWhitelist.delete(b);
    return ok(res, { phone: b }, '已移除管理员');
  }

  /* --------------------- 管理端：运营审计（P1-15） --------------------- */

  // GET /admin/audit
  if (head === 'admin' && a === 'audit' && !b && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const q = url.searchParams;
    const limit = Math.min(Math.max(Number(q.get('limit') ?? 20) || 20, 1), 100);
    const cursor = q.get('cursor');
    const target = q.get('target');
    let rows = [...auditLog].sort((x, y) => y.id - x.id); // 新的在前
    if (target) rows = rows.filter((r) => r.target === target);
    if (cursor) {
      if (!/^\d+$/.test(cursor)) return fail(res, 400, 40003, '分页游标无效');
      rows = rows.filter((r) => r.id < Number(cursor));
    }
    const hasMore = rows.length > limit;
    const items = rows.slice(0, limit);
    return ok(res, {
      list: items,
      hasMore,
      nextCursor: hasMore && items.length ? String(items[items.length - 1].id) : null,
    });
  }

  fail(res, 404, 40404, '接口不存在');
});

server.listen(PORT, () => {
  console.log(`[mock-api] 明日头条 mock 后端已启动: http://localhost:${PORT}/api`);
  console.log('[mock-api] 演示账号: 任意 11 位手机号 + 密码 123456');
});
