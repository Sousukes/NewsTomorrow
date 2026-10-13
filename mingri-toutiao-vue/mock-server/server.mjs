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
      likes: 12,
      liked: false,
      userId: null,
      mine: false,
      parentId: null,
      rootId: null,
      _seq: 6,
    },
  ],
};

/** 回复 → 输出形态：附「回复 @谁」（父行查找），无嵌套计数 */
function replyOut(r, parents) {
  const p = r.parentId ? parents.get(r.parentId) : null;
  // _seq 仅用于服务端排序，输出前剥掉（浅拷贝 + delete；解构丢弃会被 lint 拦）
  const out = {
    ...r,
    replyCount: 0,
    replies: [],
    replyToName: p?.name ?? null,
    replyToUserId: p?.userId ?? null,
  };
  delete out._seq;
  return out;
}

/** 主评论 → 输出形态：replyCount + 最早 2 条回复预览（与后端 _decorate_roots 一致） */
function commentOut(root, all) {
  const parents = new Map(all.map((x) => [x.id, x]));
  const replies = all
    .filter((x) => x.rootId === root.id && !x.isDeleted)
    .sort((x, y) => x._seq - y._seq);
  const out = {
    ...root,
    replyCount: replies.length,
    replies: replies.slice(0, 2).map((r) => replyOut(r, parents)),
  };
  delete out._seq;
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
const isAdminPhone = (phone) => ADMIN_PHONES.includes(String(phone ?? ''));

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

const freshSources = () =>
  BUILTIN_SOURCES.map((s) => ({
    ...s,
    lastStatus: null,
    lastError: null,
    lastItemCount: null,
    lastFetchedAt: null,
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
        String(n.title ?? '')
          .toLowerCase()
          .includes(kw) ||
        String(n.summary ?? '')
          .toLowerCase()
          .includes(kw),
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
    if (!item) return fail(res, 404, 40400, '内容不存在或已下架');
    return ok(res, item);
  }

  // GET /news/:id/comments（P1-12：只返回主评论，附回复数与最早 2 条预览）
  if (head === 'news' && a && b === 'comments' && !c && method === 'GET') {
    const all = comments[a] ?? [];
    const roots = all.filter((x) => !x.rootId && !x.isDeleted).sort((x, y) => y._seq - x._seq);
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
    if (!root || root.isDeleted) {
      return ok(res, { list: [], hasMore: false, nextCursor: null });
    }
    const parents = new Map(all.map((x) => [x.id, x]));
    const replies = all
      .filter((x) => x.rootId === c && !x.isDeleted)
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
    const { content, parentId } = await readBody(req);
    const text = String(content ?? '').trim();
    if (!text) return fail(res, 400, 40002, '评论内容不能为空');

    const all = comments[a] ?? [];
    let parent = null;
    if (parentId) {
      parent = all.find((x) => x.id === parentId);
      // 与后端一致：被回复评论必须存在、未删除（同文章由路径保证）
      if (!parent || parent.isDeleted) return fail(res, 400, 40003, '回复的评论不存在');
    }
    const created = {
      id: `c_${Date.now()}`,
      name: '我',
      seed: 'me',
      content: text,
      time: '刚刚',
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
    if (!target || target.isDeleted) return fail(res, 404, 40401, '评论不存在');
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
    if (!requireAdmin(req, res)) return;
    sources = freshSources();
    return ok(res, { seeded: sources.length });
  }

  // GET /admin/sources
  if (head === 'admin' && a === 'sources' && !b && method === 'GET') {
    if (!requireAdmin(req, res)) return;
    const list = [...sources].sort((x, y) => y.weight - x.weight || x.key.localeCompare(y.key));
    return ok(res, { list: list.map(sourceOut), total: list.length });
  }

  // POST /admin/sources
  if (head === 'admin' && a === 'sources' && !b && method === 'POST') {
    if (!requireAdmin(req, res)) return;
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
      lastStatus: null,
      lastError: null,
      lastItemCount: null,
      lastFetchedAt: null,
    };
    sources.push(row);
    return ok(res, sourceOut(row));
  }

  // PATCH /admin/sources/:key
  if (head === 'admin' && a === 'sources' && b && method === 'PATCH') {
    if (!requireAdmin(req, res)) return;
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
    for (const f of ['name', 'channel', 'url', 'weight', 'enabled']) {
      if (body[f] !== undefined) row[f] = body[f];
    }
    return ok(res, sourceOut(row));
  }

  // DELETE /admin/sources/:key
  if (head === 'admin' && a === 'sources' && b && method === 'DELETE') {
    if (!requireAdmin(req, res)) return;
    const i = sources.findIndex((s) => s.key === b);
    if (i < 0) return fail(res, 404, 40402, '源不存在');
    const [row] = sources.splice(i, 1);
    return ok(res, { key: row.key });
  }

  fail(res, 404, 40404, '接口不存在');
});

server.listen(PORT, () => {
  console.log(`[mock-api] 明日头条 mock 后端已启动: http://localhost:${PORT}/api`);
  console.log('[mock-api] 演示账号: 任意 11 位手机号 + 密码 123456');
});
