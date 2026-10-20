/**
 * 前后端契约兼容性验证脚本（Node，零依赖）
 *
 * 复刻前端 `src/api/http.ts` 的解包/鉴权逻辑与 `src/api/news.ts` 的字段归一化，
 * 对 FastAPI 后端（或本地 mock 后端）跑一遍，确认契约完全兼容（字段名、错误码、鉴权头）。
 *
 * 覆盖：资讯列表/详情/排序、搜索与热搜、登录、评论（含二级评论：回复结构 /
 * 正序分页 / 预览 / 删除语境）、点赞、收藏历史、RSS 源管理、管理员白名单、
 * 源健康历史与趋势（P1-19）、资讯审核与下架（P1-20）、评论治理（P1-22）、
 * 评论敏感词（P1-23）、用户昵称敏感词校验（P1-24）、令牌双轨与续期（P1-38/C1）、
 * 高敏操作二次校验（P1-40/C10）、用户举报（P1-65/E5）、评论排序（E6）、
 * 关注来源与关注流（P1-68/E6b）、搜索联想三源（P1-70/E2）。
 *
 * 用法：先启动后端，再执行 node scripts/contract-check.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const BASE = process.env.API_BASE || 'http://127.0.0.1:8000/api';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 前端常量文件（P1-85：枚举逐值校验从这里取值，不在脚本里抄第二份） */
const channelsFile = fileURLToPath(new URL('../src/constants/channels.ts', import.meta.url));
const reasonsFile = fileURLToPath(new URL('../src/constants/reports.ts', import.meta.url));
const commentSortsFile = fileURLToPath(new URL('../src/constants/comments.ts', import.meta.url));

/**
 * 从前端常量文件读出枚举取值。
 *
 * 两种形态都支持：`['a','b']`（举报理由）与 `[{ key: 'a', label: '甲' }]`
 * （频道 / 评论排序）。**不在本脚本里维护一份拷贝**——脚本自己抄一份就成了
 * 第四处来源，后端改了值时脚本会照旧"通过"，那这条校验就白写了。
 */
function readConstValues(file, constName) {
  const text = readFileSync(file, 'utf8');
  const at = text.indexOf(`export const ${constName}`);
  if (at < 0) throw new Error(`常量文件里找不到 ${constName}（${file}）`);
  const open = text.indexOf('[', at);
  const close = text.indexOf(']', open);
  if (open < 0 || close < 0) throw new Error(`${constName} 不是数组字面量（${file}）`);
  const body = text.slice(open + 1, close);
  const keyed = [...body.matchAll(/key:\s*'([^']+)'/g)].map((m) => m[1]);
  if (keyed.length) return keyed;
  return [...body.matchAll(/^\s*'([^']+)'/gm)].map((m) => m[1]);
}

/**
 * C10 受护端点：请求自动带上 step-up 头（与前端 `api/http.ts` 同一套行为——
 * 客户端不该在每个调用点手工决定"这次要不要二次校验"）。
 * 需要**故意**验证 40304 的用例传 `stepUp: false` 退出。
 */
const STEP_UP_PATTERNS = [
  /^\/me\/nickname$/,
  /^\/auth\/password$/,
  /^\/admin\/users(\/|$)/,
  /^\/admin\/sources\/[^/]+$/,
  /^\/admin\/sources\/reset$/,
];

/** 登录后由 step-up 流程填充（模块级：`request()` 在任何作用域都能读到） */
let suHeaders = {};

/* ---- 复刻 api/http.ts 的 request() ---- */
async function request(path, { method = 'GET', body, token, headers: extra = {}, stepUp } = {}) {
  const headers = { 'Content-Type': 'application/json', ...extra };
  if (token) headers.Authorization = `Bearer ${token}`;
  const guarded = stepUp ?? STEP_UP_PATTERNS.some((re) => re.test(path));
  if (guarded && suHeaders['X-Step-Up-Token']) {
    headers['X-Step-Up-Token'] = suHeaders['X-Step-Up-Token'];
  }
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const err = await res.json();
      if (err?.message) message = err.message;
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  const json = await res.json();
  if (json && typeof json === 'object' && 'code' in json) {
    if (json.code !== 0 && json.code !== 200) throw new Error(json.message || '接口返回错误');
    return json.data;
  }
  return json;
}

/* ---- 复刻 api/news.ts 的 normalize()（校验后端字段能被前端识别）---- */
function normalize(raw) {
  return {
    id: String(raw.id ?? raw.newsId ?? ''),
    title: String(raw.title ?? ''),
    summary: raw.summary ?? raw.digest ?? undefined,
    // E1：正文只由 GET /news/:id 详情下发，按段落 \n 分隔
    content: raw.content ?? undefined,
    source: String(raw.source ?? raw.mediaName ?? ''),
    commentCount: Number(raw.commentCount ?? raw.comment_count ?? 0),
    time: String(raw.time ?? raw.publishTime ?? ''),
    images: raw.images ?? raw.imageList ?? undefined,
    cover: raw.cover ?? raw.coverUrl ?? undefined,
    videoUrl: raw.videoUrl ?? undefined,
    playCount: raw.playCount ?? undefined,
    isTop: Boolean(raw.isTop ?? false),
    channel: raw.channel ?? 'recommend',
    variant: raw.variant ?? 'left-right',
  };
}

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) {
    pass++;
    console.log(`  PASS  ${name}${extra ? ' — ' + extra : ''}`);
  } else {
    fail++;
    console.log(`  FAIL  ${name}${extra ? ' — ' + extra : ''}`);
  }
}

const run = async () => {
  console.log(`\n=== 契约检查 @ ${BASE} ===\n`);

  // 1) 资讯列表 + 字段归一化
  const listData = await request('/news?channel=tech');
  const items = (listData.list ?? []).map(normalize);
  check('GET /news 返回列表', items.length > 0, `${items.length} 条`);
  check(
    '字段可被前端识别（id/title/variant）',
    items.every((i) => i.id && i.title && i.variant),
    items[0]?.variant,
  );
  check('commentCount 为数字', typeof items[0]?.commentCount === 'number');
  // E1：正文只在详情下发——列表带正文会让一页 20 条多出几十 KB 无效带宽
  check(
    '列表响应不带正文（content 仅详情返回）',
    (listData.list ?? []).every((n) => n.content === undefined),
  );

  // 1.1) 分页形态（B2）：默认 keyset（不做全表 count），显式 page 才走兼容分支
  check(
    '默认不传 page → total 为 null、page 为 null（B2：首页不 count）',
    listData.total === null && listData.page === null,
    `total=${String(listData.total)}`,
  );
  check(
    '默认响应带 hasMore / nextCursor 游标字段',
    'hasMore' in listData && 'nextCursor' in listData,
  );
  const legacyPage = await request('/news?channel=tech&page=1&pageSize=3');
  check(
    '显式传 page → 走 offset 兼容分支且 total 有值',
    typeof legacyPage.total === 'number' && legacyPage.total > 0 && legacyPage.page === 1,
    `${legacyPage.total} 条`,
  );
  if (listData.nextCursor) {
    const second = await request(
      `/news?channel=tech&cursor=${encodeURIComponent(listData.nextCursor)}`,
    );
    check(
      '游标续页返回数据且 total 仍为 null（B2）',
      Array.isArray(second.list) && second.total === null,
      `${(second.list ?? []).length} 条`,
    );
  }

  // 1.2) 图片代理（B4）：只验**安全策略**，不验"能取到图"
  //     ——真后端取图要出网，契约环境无外网；取图行为由后端单测覆盖。
  //     这里是 SSRF 防线，必须双端一致。
  const imgRejects = async (u) => {
    try {
      await request(`/img?url=${encodeURIComponent(u)}`);
      return '(未拒绝)';
    } catch (e) {
      return e.message;
    }
  };
  const imgFile = await imgRejects('file:///etc/passwd');
  check('图片代理拒绝非 http(s) 协议（B4/SSRF）', imgFile.includes('只支持'), imgFile);
  const imgLocal = await imgRejects('http://127.0.0.1:8000/x.png');
  check('图片代理拒绝本机地址（B4/SSRF）', imgLocal.includes('内网'), imgLocal);
  const imgMeta = await imgRejects('http://169.254.169.254/latest/meta-data/');
  check('图片代理拒绝云元数据地址 169.254.169.254（B4/SSRF）', imgMeta.includes('内网'), imgMeta);
  const imgNone = await imgRejects('');
  check('图片代理拒绝缺 url 参数（B4）', imgNone.includes('缺少图片地址'), imgNone);

  // 2) 资讯详情
  const detail = normalize(await request(`/news/${items[0].id}`));
  check('GET /news/:id 详情', detail.id === items[0].id);
  // E1：正文字段必须是 string 或 null/undefined（无正文的老数据与没给的源），
  // 其他类型（数字/对象）说明前后端契约漂移
  check(
    '详情 content 为字符串或为空',
    detail.content == null || typeof detail.content === 'string',
    detail.content == null ? '无正文' : `${String(detail.content).length} 字符`,
  );

  // 2.1) 排序三模式：sort 取值必须与前端 constants/sort.ts 一致（漂移会 422）
  const hotList = await request('/news?sort=hot&pageSize=3');
  check('GET /news?sort=hot 返回列表', (hotList.list ?? []).length > 0);
  check('sort=hot 回显 hot', hotList.sort === 'hot', String(hotList.sort));
  const forYou = await request('/news?sort=for-you&pageSize=3');
  check('GET /news?sort=for-you 未登录退化为可用列表', (forYou.list ?? []).length > 0);
  check('sort=for-you 回显 for-you', forYou.sort === 'for-you', String(forYou.sort));
  let badSort = false;
  try {
    await request('/news?sort=bogus');
  } catch {
    badSort = true; // 后端 422 → request 抛错
  }
  check('非法 sort 被拒绝（422）', badSort);

  // 2.2) 热搜词：真实统计（前端不应再展示写死的假热词）
  const hot = await request('/news/search/hot?limit=10');
  check('GET /news/search/hot 返回 list', Array.isArray(hot.list), `${hot.list?.length ?? 0} 条`);
  check(
    '热词项含 term 与数字 count',
    hot.list.every?.((t) => typeof t?.term === 'string' && typeof t?.count === 'number') ?? true,
    hot.list?.[0]?.term,
  );
  // 搜索过的词应出现在热搜榜（契约脚本自身搜过 keyword='x'）
  await request('/news/search?q=契约热词');
  const afterSearch = await request('/news/search/hot?limit=50');
  check(
    '搜索后的词进入热搜统计',
    (afterSearch.list ?? []).some((t) => t.term === '契约热词'),
  );

  // 2.3) 搜索联想（E2 / P1-70）：三源、字段名、以及"不写日志"这条硬约束
  const sEmpty = await request('/search/suggest?q=');
  check(
    '空输入 → 200 + 空列表（不是错误）',
    sEmpty.list?.length === 0 && sEmpty.keyword === '',
    JSON.stringify(sEmpty),
  );
  check(
    '纯空格 → keyword 归一化为空串',
    (await request('/search/suggest?q=%20%20')).keyword === '',
  );

  // 联想绝不能写 `search_logs`：边打字就把"新能源汽""新能源汽⻋"这类中间态
  // 写进榜里，热搜会被输入过程污染（词越长，中间态越多）。
  //
  // 计数用**不同的 days** 读两次：热搜榜有 60s 缓存（D6），同一个键的第二次
  // 读会命中缓存并返回上一份结果——那样"有没有新增"根本测不出来，
  // 断言会变成恒真。days 参与缓存键，换一个值就是一次真实查询。
  const countOf = async (term, days) =>
    (await request(`/news/search/hot?days=${days}&limit=50`)).list.find((t) => t.term === term)
      ?.count ?? 0;
  // 探针词的**前缀每次运行都不同**。此前所有运行共用「联想探针」前缀，dev 库的
  // search_logs 会逐次累积探针词，而 hot 源按「次数倒序 + 词典序」取前 N 条：
  // 10 个旧探针（次数都是 1）就会把当次探针挤出前十，断言于是变成「看库有多脏」
  // 而不是「看联想好不好用」。换成唯一前缀后，只有当次探针能匹配上前缀。
  const tag = `探${String(Date.now()).slice(-6)}`;
  const probe = `${tag}针`;
  await request(`/news/search?q=${encodeURIComponent(probe)}`);
  const probeBefore = await countOf(probe, 6);
  for (const q of [tag.slice(0, 2), tag.slice(0, 4), probe]) {
    await request(`/search/suggest?q=${encodeURIComponent(q)}`);
  }
  const probeAfter = await countOf(probe, 5);
  check(
    '联想不写 search_logs（热搜计数不变）',
    probeBefore === 1 && probeAfter === 1,
    `${probeBefore} → ${probeAfter}`,
  );

  // hot 源：搜过的词按前缀回来
  const sHot = await request(`/search/suggest?q=${encodeURIComponent(tag)}`);
  check(
    'hot 源：搜过的词按前缀出现',
    (sHot.list ?? []).some((i) => i.term === probe && i.kind === 'hot'),
    JSON.stringify(sHot.list?.slice(0, 3)),
  );

  // correct 源：错一个字能回到真实搜过的词（编辑距离 1）
  const typo = `${probe.slice(0, -1)}X`;
  const sCorrect = await request(`/search/suggest?q=${encodeURIComponent(typo)}`);
  check(
    'correct 源：错一个字回到真实词',
    (sCorrect.list ?? []).some((i) => i.term === probe && i.kind === 'correct'),
    JSON.stringify(sCorrect.list?.slice(0, 3)),
  );

  // title 源：双端种子标题一致（n5「新能源汽车出口…」以"新能源"开头）
  const sTitle = await request(`/search/suggest?q=${encodeURIComponent('新能源')}`);
  check(
    'title 源：标题前缀出现',
    (sTitle.list ?? []).some((i) => i.kind === 'title' && i.term.startsWith('新能源')),
    JSON.stringify(sTitle.list?.slice(0, 3)),
  );
  check(
    '联想项只含 term / kind / hitCount（不下发资讯内容）',
    sTitle.list?.length > 0 &&
      sTitle.list.every((i) => Object.keys(i).sort().join() === 'hitCount,kind,term'),
    JSON.stringify(sTitle.list?.[0]),
  );

  // 边界：与 FastAPI 的 `ge=1, le=20` 同口径，mock 也必须**拒绝**而非夹取
  let badLimit = false;
  try {
    await request('/search/suggest?q=a&limit=0');
  } catch {
    badLimit = true;
  }
  check('limit=0 被拒（422）', badLimit);
  let overLimit = false;
  try {
    await request('/search/suggest?q=a&limit=21');
  } catch {
    overLimit = true;
  }
  check('limit=21 被拒（上限 20）', overLimit);

  // 3) 登录错误密码 → 前端展示 S16 文案
  let s16 = '';
  try {
    await request('/auth/login', {
      method: 'POST',
      body: { phone: '13800138000', password: '__bad__' },
    });
  } catch (e) {
    s16 = e.message;
  }
  check('错误密码抛错且文案匹配 S16', s16 === '手机号或密码错误，请重新输入', s16);

  // 4) 正确密码登录（从环境变量传入，避免硬编码）
  const phone = process.env.DEMO_PHONE || '13800138000';
  const password = process.env.DEMO_PASSWORD;
  check('拿到演示密码', !!password);
  let token = '';
  if (password) {
    const auth = await request('/auth/login', { method: 'POST', body: { phone, password } });
    token = auth.token;
    check('POST /auth/login 返回 token+user', !!token && !!auth.user?.name, auth.user?.name);

    // C1 令牌双轨：access（短，业务接口）/ refresh（长，只能换 access）
    const refreshToken = auth.refreshToken ?? '';
    check(
      '登录下发 refreshToken 与 expiresIn',
      !!refreshToken && Number(auth.expiresIn) > 0,
      `expiresIn=${auth.expiresIn}`,
    );

    // 类型隔离是双轨方案的前提：refresh 若能被业务接口接受，「短 access」形同虚设
    let rtRejected = false;
    try {
      await request('/me/favorites', { token: refreshToken });
    } catch (e) {
      rtRejected = e.message === '未登录或登录已过期';
    }
    check('refreshToken 访问业务接口 → 401', rtRejected);

    // 换发：access 过期后靠 refresh 静默续期，用户不掉线
    const renewed = await request('/auth/refresh', {
      method: 'POST',
      body: { refreshToken },
    });
    check('用 refreshToken 换发新 access', !!renewed.token && renewed.token !== token);
    const renewedMe = await request('/me/favorites', { token: renewed.token });
    check(
      '换发后的 access 可用（是 access 而非 refresh）',
      Array.isArray(renewedMe.ids) && Array.isArray(renewedMe.list),
    );

    // 7) 高敏操作二次校验（C10）：缺令牌 40304，换发后放行
    let stepUpRequired = false;
    try {
      // stepUp: false —— 这一条就是要验证「没带令牌会被拦」
      await request('/me/nickname', {
        method: 'PUT',
        body: { name: '契约校验昵称' },
        token,
        stepUp: false,
      });
    } catch (e) {
      stepUpRequired = e.message === '此操作需要二次校验，请先验证身份';
    }
    check('改名缺二次校验令牌 → 40304', stepUpRequired);

    const stepUp = await request('/auth/step-up', {
      method: 'POST',
      body: { password },
      token,
    });
    check('二次校验换取 step-up 令牌', !!stepUp.stepUpToken && stepUp.expiresIn > 0);
    suHeaders = { 'X-Step-Up-Token': stepUp.stepUpToken };

    const nicknamed = await request('/me/nickname', {
      method: 'PUT',
      body: { name: '契约校验昵称' },
      token,
      headers: suHeaders,
    });
    check('带 step-up 令牌可改名', nicknamed.result !== undefined);

    // 8) 自助改密（C4）：用一个**临时账号**跑完整闭环，不动演示账号
    //    （后续若干段还会用演示账号重新登录，改了它整场契约都会崩）。
    //    手机号按运行时间生成：固定号码第二次跑会撞 409（账号已注册）。
    const runTag = String(Date.now()).slice(-8);
    const c4Phone = `139${runTag}`;
    const c4Old = 'c4-old-pass';
    const c4New = 'c4-new-pass';
    const regC4 = await request('/auth/register', {
      method: 'POST',
      body: { phone: c4Phone, password: c4Old, name: '改密临时账号' },
    });
    check('注册改密用临时账号', regC4.token !== undefined);
    const c4Login = await request('/auth/login', {
      method: 'POST',
      body: { phone: c4Phone, password: c4Old },
    });
    check('新账号登录默认不受限（C4）', c4Login.user.mustChangePassword === false);

    let noStepUp = false;
    try {
      await request('/auth/password', {
        method: 'PUT',
        body: { oldPassword: c4Old, newPassword: c4New },
        token: c4Login.token,
        stepUp: false,
      });
    } catch (e) {
      noStepUp = e.message === '此操作需要二次校验，请先验证身份';
    }
    check('改密缺二次校验令牌 → 40304（C4/C10）', noStepUp);

    const c4Su = await request('/auth/step-up', {
      method: 'POST',
      body: { password: c4Old },
      token: c4Login.token,
    });
    const c4SuHeaders = { 'X-Step-Up-Token': c4Su.stepUpToken };

    let wrongOld = false;
    try {
      await request('/auth/password', {
        method: 'PUT',
        body: { oldPassword: 'definitely-wrong', newPassword: c4New },
        token: c4Login.token,
        headers: c4SuHeaders,
      });
    } catch (e) {
      wrongOld = e.message === '原密码不正确';
    }
    check('改密旧密码错误 → 40101', wrongOld);

    const changed = await request('/auth/password', {
      method: 'PUT',
      body: { oldPassword: c4Old, newPassword: c4New },
      token: c4Login.token,
      headers: c4SuHeaders,
    });
    check('改密成功并下发新令牌对', !!changed.token && !!changed.refreshToken);
    // 旧 access 已作废（改密 = 作废旧凭证）
    let oldTokenDead = false;
    try {
      await request('/me/favorites', { token: c4Login.token });
    } catch {
      oldTokenDead = true;
    }
    check('改密后旧 access 立即失效', oldTokenDead);
    const meAfter = await request('/me/favorites', { token: changed.token });
    check('改密后新 access 可用', Array.isArray(meAfter.ids));
    let oldPwdDead = false;
    try {
      await request('/auth/login', { method: 'POST', body: { phone: c4Phone, password: c4Old } });
    } catch {
      oldPwdDead = true;
    }
    const c4NewLogin = await request('/auth/login', {
      method: 'POST',
      body: { phone: c4Phone, password: c4New },
    });
    // 自助改密不是「代重置」，不该留下受限态
    check(
      '旧密码不能登录、新密码可以且不受限（C4）',
      oldPwdDead && c4NewLogin.user.mustChangePassword === false,
    );
  }

  // 5) 鉴权：无 token 发评论应被拒
  let unauthorized = false;
  try {
    await request('/news/n4/comments', { method: 'POST', body: { content: 'x' } });
  } catch (e) {
    unauthorized = e.message === '未登录或登录已过期';
  }
  check('无 token 发评论 → 401 且透传提示', unauthorized);

  if (token) {
    // 6) 带 token 发评论
    const created = await request('/news/n4/comments', {
      method: 'POST',
      body: { content: '契约检查评论' },
      token,
    });
    check('带 token 发评论成功', !!created.id && created.content === '契约检查评论');

    // 7) 点赞（幂等：重复点赞不应重复计数）
    const first = await request(`/news/n4/comments/${created.id}`, {
      method: 'PATCH',
      body: { liked: true },
      token,
    });
    const second = await request(`/news/n4/comments/${created.id}`, {
      method: 'PATCH',
      body: { liked: true },
      token,
    });
    check(
      '点赞 likes=1 且重复点赞不累加',
      first.likes === 1 && second.likes === 1,
      `${first.likes}/${second.likes}`,
    );
    check('liked=true', second.liked === true);

    // 8) 评论列表 liked 按当前用户计算
    // 评论已改为游标分页，返回 { list, hasMore, nextCursor }——需与 /api/news 一样解包，
    // 直接对返回值调 .find 会在对象上报错并中断后续检查。
    const commentData = await request('/news/n4/comments', { token });
    const commentList = commentData.list ?? [];
    const mine = commentList.find((c) => c.id === created.id);
    check('列表返回 liked 标记', mine?.liked === true);

    // 8.1) 二级评论契约（P1-12）
    //     回复创建 → rootId 拍平；replies 正序分页；主列表只出主评论 + 预览；
    //     删主评论后其回复不再可见。注意：后端 created_at 为秒级精度，
    //     两条回复之间须错开 1s 以上，「正序」断言才不受同秒随机 id 兜底影响。
    const reply1 = await request('/news/n4/comments', {
      method: 'POST',
      body: { content: '契约检查回复一', parentId: created.id },
      token,
    });
    check('回复创建：parentId 指向被回复者', reply1.parentId === created.id);
    check('回复创建：rootId 指向主评论', reply1.rootId === created.id);
    check(
      '回复创建：replyToName 为被回复者名',
      reply1.replyToName === created.name,
      String(reply1.replyToName),
    );

    await sleep(1100); // 秒级时间戳：错开创建时间，正序才确定
    const reply2 = await request('/news/n4/comments', {
      method: 'POST',
      body: { content: '契约检查回复二', parentId: reply1.id },
      token,
    });
    check('回复的回复：parentId 指向被回复的回复', reply2.parentId === reply1.id);
    check('回复的回复：rootId 拍平到同一主评论', reply2.rootId === created.id);

    const repData = await request(`/news/n4/comments/${created.id}/replies`, { token });
    const repList = repData.list ?? [];
    check(
      'replies 返回 {list,hasMore,nextCursor}',
      Array.isArray(repData.list) && typeof repData.hasMore === 'boolean',
    );
    check('replies 恰好返回本次两条回复', repList.length === 2, `${repList.length} 条`);
    check(
      'replies 按时间正序（先发的在前）',
      repList[0]?.id === reply1.id && repList[1]?.id === reply2.id,
    );
    check(
      'replies 附带「回复 @谁」',
      repList[1]?.replyToName === reply1.name,
      String(repList[1]?.replyToName),
    );
    check(
      'replies 项 rootId 均指向主评论',
      repList.every((r) => r.rootId === created.id),
    );

    const main2 = await request('/news/n4/comments', { token });
    const mainList2 = main2.list ?? [];
    check(
      '主列表只含主评论（rootId 均为 null）',
      mainList2.every((c) => c.rootId === null),
    );
    check('回复不出现在主列表', !mainList2.some((c) => c.id === reply1.id || c.id === reply2.id));
    const rootOut = mainList2.find((c) => c.id === created.id);
    check(
      '主评论 replyCount 含全部回复',
      (rootOut?.replyCount ?? 0) >= 2,
      String(rootOut?.replyCount),
    );
    check('主评论预览最多 2 条', (rootOut?.replies ?? []).length <= 2);
    check(
      '预览为最早 2 条',
      rootOut?.replies?.[0]?.id === reply1.id && rootOut?.replies?.[1]?.id === reply2.id,
    );

    const likedReply = await request(`/news/n4/comments/${reply1.id}`, {
      method: 'PATCH',
      body: { liked: true },
      token,
    });
    check('回复可点赞且 likes=1', likedReply.likes === 1 && likedReply.liked === true);
    const repAfterLike = await request(`/news/n4/comments/${created.id}/replies`, { token });
    check('回复列表 liked 按当前用户标记', repAfterLike.list?.[0]?.liked === true);

    // 删除主评论 → 其回复不再可见（回复行保留但失去语境不展示）
    await request(`/news/n4/comments/${created.id}`, { method: 'DELETE', token });
    const afterDel = await request(`/news/n4/comments/${created.id}/replies`, { token });
    check('删除主评论后 replies 返回空', (afterDel.list ?? []).length === 0);
    const main3 = await request('/news/n4/comments', { token });
    check('删除后主列表不含该主评论', !(main3.list ?? []).some((c) => c.id === created.id));

    // 8.2) 评论排序（E6）：latest（默认）/ hot；游标携带 sort（否则跨排序续页会静默错位）
    const latestPage = await request('/news/n4/comments?sort=latest', { token });
    check('sort=latest 返回列表', Array.isArray(latestPage.list));
    const hotPage = await request('/news/n4/comments?sort=hot', { token });
    const hotLikes = (hotPage.list ?? []).map((c) => Number(c.likes ?? 0));
    check(
      'sort=hot 按点赞数不增',
      hotLikes.every((n, i) => i === 0 || hotLikes[i - 1] >= n),
      hotLikes.join(','),
    );
    let badSort = '';
    try {
      await request('/news/n4/comments?sort=bogus', { token });
    } catch (e) {
      badSort = e.message;
    }
    check('非法 sort → 42201 提示', badSort.includes('排序方式不支持'), badSort);
    const onePage = await request('/news/n4/comments?sort=hot&limit=1', { token });
    if (onePage.hasMore && onePage.nextCursor) {
      let payload = null;
      try {
        payload = JSON.parse(Buffer.from(onePage.nextCursor, 'base64').toString('utf8'));
      } catch {
        /* 非 base64 → 下面断言失败即可 */
      }
      check(
        '游标为 v2 且携带 sort',
        Array.isArray(payload) && payload[0] === 2 && payload[1] === 'hot',
        JSON.stringify(payload),
      );
    }
  }

  // 8.9) 用户举报（E5 / P1-65）：前端只依赖 POST /reports 的 `{ status, autoHidden }`
  //
  // 刻意举报**演示资讯 n4**，而不是每轮新建的评论：举报没有用户侧删除接口，
  // 固定目标才能让每次运行落在同一行（同一用户同一目标幂等），不累积垃圾数据。
  // 阈值默认 3 且按**不同举报人**去重，单账号重复举报不会误隐藏演示内容。
  let reportUnauthorized = false;
  try {
    await request('/reports', {
      method: 'POST',
      body: { targetType: 'news', targetId: 'n4', reason: '广告营销' },
    });
  } catch (e) {
    reportUnauthorized = e.message === '未登录或登录已过期';
  }
  check('无 token 举报 → 401 且透传提示', reportUnauthorized);

  if (token) {
    const reported = await request('/reports', {
      method: 'POST',
      body: { targetType: 'news', targetId: 'n4', reason: '广告营销' },
      token,
    });
    check(
      '举报成功返回 { status, autoHidden }',
      reported?.status === 'pending' && reported?.autoHidden === false,
      `${reported?.status}/${reported?.autoHidden}`,
    );

    // 幂等：同一用户换理由再报 → 200 且不触发自动处置（不新增行，仍只算 1 人）
    const reportAgain = await request('/reports', {
      method: 'POST',
      body: { targetType: 'news', targetId: 'n4', reason: '其他' },
      token,
    });
    check(
      '重复举报幂等（不新增行、不触发自动处置）',
      reportAgain?.status === 'pending' && reportAgain?.autoHidden === false,
    );

    const expectError = async (body, expected) => {
      try {
        await request('/reports', { method: 'POST', body, token });
        return `未被拒绝（期望「${expected}」）`;
      } catch (e) {
        return e.message;
      }
    };
    const badReason = await expectError(
      { targetType: 'news', targetId: 'n4', reason: '不存在的理由' },
      '举报理由不合法',
    );
    check('非法理由 → 42206 提示', badReason === '举报理由不合法', badReason);

    const badType = await expectError(
      { targetType: 'user', targetId: 'n4', reason: '其他' },
      '举报对象类型不合法',
    );
    check('非法对象类型 → 42206 提示', badType === '举报对象类型不合法', badType);

    const missingTarget = await expectError(
      { targetType: 'news', targetId: 'n__not_exist', reason: '其他' },
      '举报对象不存在或已删除',
    );
    check('目标不存在 → 40409 提示', missingTarget === '举报对象不存在或已删除', missingTarget);
  }

  // 8.95) 关注来源（E6b）：源目录 + /me/follows + 关注流
  //
  // 收尾会取关，不留残留；关注流断言用「不含该源」而不是「整体为空」——
  // 开发库里可能本来就有关注关系，契约检查不该假设干净状态。
  const dir = await request('/sources');
  // 优先挑一个**有内容**的来源：关注一个空来源，"关注流只含该源"会在空列表上
  // 恒真，等于没验。有内容才验得动过滤逻辑。
  const dirList = dir.list ?? [];
  const firstSource = dirList.find((s) => s.newsCount > 0) ?? dirList[0];
  check(
    'GET /sources 返回来源目录（公开）',
    !!firstSource && 'key' in firstSource && 'newsCount' in firstSource,
    firstSource?.name,
  );
  check('来源目录不暴露 url（运营信息）', !('url' in (firstSource ?? {})));

  let followUnauthorized = false;
  try {
    await request('/me/follows');
  } catch (e) {
    followUnauthorized = e.message === '未登录或登录已过期';
  }
  check('无 token 访问 /me/follows → 401', followUnauthorized);

  if (token && firstSource) {
    const followed = await request('/me/follows', {
      method: 'POST',
      body: { sourceKey: firstSource.key },
      token,
    });
    check(
      '关注成功返回 { keys, list }',
      Array.isArray(followed.keys) && followed.keys.includes(firstSource.key),
    );
    const again = await request('/me/follows', {
      method: 'POST',
      body: { sourceKey: firstSource.key },
      token,
    });
    check(
      '重复关注幂等（keys 不重复）',
      again.keys.filter((k) => k === firstSource.key).length === 1,
    );

    const feedPage = await request('/news?feed=following&pageSize=5', { token });
    check(
      '关注流只含已关注来源',
      (feedPage.list ?? []).every((n) => n.sourceKey === firstSource.key),
      `${(feedPage.list ?? []).length} 条`,
    );
    if (firstSource.newsCount > 0) {
      check(
        '关注流返回该来源的内容（过滤不是"全空"）',
        (feedPage.list ?? []).length > 0,
        `newsCount=${firstSource.newsCount}`,
      );
    }
    check(
      '关注流条目带 sourceKey（前端据此判断"已关注"）',
      (feedPage.list ?? []).every((n) => typeof n.sourceKey === 'string'),
    );

    let badFeed = '';
    try {
      await request('/news?feed=bogus', { token });
    } catch (e) {
      badFeed = e.message;
    }
    check('非法 feed → 42200 提示', badFeed.includes('不支持的 feed'), badFeed);

    const unfollowed = await request(`/me/follows/${firstSource.key}`, {
      method: 'DELETE',
      token,
    });
    check('取关后返回空集合（幂等）', !unfollowed.keys.includes(firstSource.key));
    const after = await request('/news?feed=following', { token });
    check(
      '取关后关注流不含该来源',
      (after.list ?? []).every((n) => n.sourceKey !== firstSource.key),
    );
  }

  // 9) 收藏 / 浏览历史云端同步（/api/me/*）
  let meUnauthorized = false;
  try {
    await request('/me/favorites');
  } catch (e) {
    meUnauthorized = e.message === '未登录或登录已过期';
  }
  check('无 token 访问 /me/favorites → 401', meUnauthorized);

  if (token) {
    const target = items[0].id;

    const initial = await request('/me/favorites', { token });
    check(
      'GET /me/favorites 返回 {ids,list}',
      Array.isArray(initial.ids) && Array.isArray(initial.list),
    );

    const added = await request('/me/favorites', {
      method: 'POST',
      body: { newsId: target },
      token,
    });
    check('POST /me/favorites 收藏成功', added.ids.includes(target), `ids=${added.ids.length}`);
    check('收藏返回完整资讯对象', added.list[0]?.id === target);

    // 并集合并：本地多传一个 id 应并入，且原有收藏不丢
    const merged = await request('/me/favorites', {
      method: 'PUT',
      body: { ids: [items[1].id, target] },
      token,
    });
    check(
      'PUT /me/favorites 并集合并且不丢数据',
      merged.ids.includes(target) && merged.ids.includes(items[1].id),
    );

    const removed = await request(`/me/favorites/${target}`, { method: 'DELETE', token });
    check('DELETE /me/favorites/:id 取消收藏', !removed.ids.includes(target));

    const recorded = await request('/me/history', {
      method: 'POST',
      body: { newsId: target },
      token,
    });
    check('POST /me/history 记录浏览', recorded.ids.includes(target));

    const cleared = await request('/me/history', { method: 'DELETE', token });
    check('DELETE /me/history 清空', cleared.ids.length === 0);

    // 收尾：清掉本次契约检查留下的收藏，避免污染开发库
    await request('/me/favorites', { method: 'PUT', body: { ids: [] }, token });
    for (const id of [target, items[1].id]) {
      await request(`/me/favorites/${id}`, { method: 'DELETE', token });
    }
  }

  // 10) 管理端 RSS 源配置（P1-9）
  //     默认跳过：这是后端专属能力，前端没有对应界面；且要求当前账号在 ADMIN_PHONES 白名单内。
  //     需要时用 CHECK_ADMIN=1 打开（后端须以 ADMIN_PHONES=<当前手机号> 启动）。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const srcData = await request('/admin/sources', { token });
    check(
      'GET /admin/sources 返回 {list,total}',
      Array.isArray(srcData.list) && srcData.total > 0,
      `${srcData.total} 个源`,
    );
    const keys = (srcData.list ?? []).map((s) => s.key);
    check('内置源含 aihot_picks（本次新增信源）', keys.includes('aihot_picks'));
    check(
      '源字段完整（key/name/url/enabled）',
      (srcData.list ?? []).every((s) => s.key && s.name && s.url && typeof s.enabled === 'boolean'),
    );

    const tmpKey = '__contract_tmp';
    const created = await request('/admin/sources', {
      method: 'POST',
      token,
      body: {
        key: tmpKey,
        name: '契约临时源',
        channel: 'tech',
        url: 'https://example.com/c.xml',
        weight: 1,
      },
    });
    check('POST /admin/sources 新增成功', created.key === tmpKey);

    const patched = await request(`/admin/sources/${tmpKey}`, {
      method: 'PATCH',
      token,
      body: { weight: 5, enabled: false },
    });
    check('PATCH 局部更新（未传字段不清空）', patched.weight === 5 && patched.enabled === false);
    check('PATCH 未传的 url 保持不变', patched.url === 'https://example.com/c.xml');

    const removedSrc = await request(`/admin/sources/${tmpKey}`, {
      method: 'DELETE',
      token,
      headers: suHeaders,
    });
    check('DELETE /admin/sources/:key 删除成功', removedSrc.key === tmpKey);

    let blockedScheme = false;
    try {
      await request('/admin/sources', {
        method: 'POST',
        token,
        body: { key: '__bad', name: 'X', channel: 'tech', url: 'file:///etc/passwd' },
      });
    } catch {
      blockedScheme = true;
    }
    check('非 http(s) 源地址被拒（抓取器会真的请求它）', blockedScheme);

    let dupBlocked = false;
    try {
      await request('/admin/sources', {
        method: 'POST',
        token,
        body: {
          key: 'aihot_picks',
          name: '重复',
          channel: 'tech',
          url: 'https://example.com/d.xml',
        },
      });
    } catch {
      dupBlocked = true;
    }
    check('重复 key 被拒', dupBlocked);
  }

  // 11) 管理端：管理员白名单（P1-14）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1 + 后端白名单内含当前手机号）。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const wlData = await request('/admin/users', { token });
    check(
      'GET /admin/users 返回 {list,total,envAdminCount}',
      Array.isArray(wlData.list) &&
        typeof wlData.total === 'number' &&
        typeof wlData.envAdminCount === 'number',
      `库内 ${wlData.total} 位 / env ${wlData.envAdminCount} 位`,
    );

    const tmpAdmin = '13912345678';
    // 清理上次异常退出可能留下的残留（失败无妨——env 非空时删除本就允许）
    try {
      await request(`/admin/users/${tmpAdmin}`, {
        method: 'DELETE',
        token,
        headers: suHeaders,
      });
    } catch {
      /* ignore */
    }

    const added = await request('/admin/users', {
      method: 'POST',
      token,
      headers: suHeaders,
      body: { phone: tmpAdmin, note: '契约临时管理员' },
    });
    check(
      'POST /admin/users 添加成功',
      added.phone === tmpAdmin && added.note === '契约临时管理员',
    );
    check('addedBy 记录操作者（审计用）', added.addedBy === phone, String(added.addedBy));

    let dupBlocked = false;
    try {
      await request('/admin/users', {
        method: 'POST',
        token,
        headers: suHeaders,
        body: { phone: tmpAdmin },
      });
    } catch {
      dupBlocked = true;
    }
    check('重复添加被拒（409）', dupBlocked);

    // 当前账号自身就在白名单里（CHECK_ADMIN 的前提），再添加自己应被拒
    let selfBlocked = false;
    try {
      await request('/admin/users', { method: 'POST', token, headers: suHeaders, body: { phone } });
    } catch {
      selfBlocked = true;
    }
    check('添加白名单内已有号码被拒', selfBlocked);

    let badPhoneBlocked = false;
    try {
      await request('/admin/users', { method: 'POST', token, body: { phone: '123' } });
    } catch {
      badPhoneBlocked = true;
    }
    check('非法手机号被拒（422）', badPhoneBlocked);

    const afterAdd = await request('/admin/users', { token });
    check(
      '添加后列表含新行',
      (afterAdd.list ?? []).some((a) => a.phone === tmpAdmin),
    );

    const removed = await request(`/admin/users/${tmpAdmin}`, {
      method: 'DELETE',
      token,
      headers: suHeaders,
    });
    check('DELETE /admin/users/:phone 移除成功', removed.phone === tmpAdmin);

    let removeMissing = false;
    try {
      await request(`/admin/users/${tmpAdmin}`, {
        method: 'DELETE',
        token,
        headers: suHeaders,
      });
    } catch {
      removeMissing = true;
    }
    check('重复移除被拒（404）', removeMissing);

    // 管理员代重置密码（C4）：对**已注册的普通用户**生效，不只是管理员
    // 手机号同样按运行时间生成：固定号码第二次跑会撞 409（账号已注册）
    const resetPhone = `138${String(Date.now()).slice(-8)}`;
    await request('/auth/register', {
      method: 'POST',
      body: { phone: resetPhone, password: 'c4-target-pass', name: '待重置用户' },
    });
    let selfReset = '';
    try {
      await request(`/admin/users/${phone}/reset-password`, { method: 'POST', token });
    } catch (e) {
      selfReset = e.message;
    }
    check('不能重置自己的密码（C4）', selfReset.includes('不能重置自己的密码'), selfReset);

    let unknownReset = '';
    try {
      await request('/admin/users/13911119999/reset-password', { method: 'POST', token });
    } catch (e) {
      unknownReset = e.message;
    }
    // 与公开接口同码：不靠错误码区分「手机号是否已注册」
    check('重置未注册手机号 → 404 且文案不区分（C4）', unknownReset === '用户不存在');

    const reset = await request(`/admin/users/${resetPhone}/reset-password`, {
      method: 'POST',
      token,
    });
    check(
      '代重置返回一次性临时密码并置受限（C4）',
      typeof reset.tempPassword === 'string' && reset.tempPassword.length >= 8,
    );
    const afterReset = await request('/auth/login', {
      method: 'POST',
      body: { phone: resetPhone, password: reset.tempPassword },
    });
    check('临时密码可登录且标记待改密（C4）', afterReset.user.mustChangePassword === true);

    let restricted = false;
    try {
      await request('/me/favorites', {
        method: 'POST',
        body: { newsId: items[0].id },
        token: afterReset.token,
      });
    } catch (e) {
      restricted = e.message === '请先修改初始密码';
    }
    check('受限账号写操作被拦 40305（C4）', restricted);

    const targetSu = await request('/auth/step-up', {
      method: 'POST',
      body: { password: reset.tempPassword },
      token: afterReset.token,
    });
    const targetChanged = await request('/auth/password', {
      method: 'PUT',
      body: { oldPassword: reset.tempPassword, newPassword: 'c4-target-new' },
      token: afterReset.token,
      headers: { 'X-Step-Up-Token': targetSu.stepUpToken },
    });
    check('受限账号改密后解除受限（C4）', targetChanged.user.mustChangePassword === false);
    const afterChange = await request('/me/favorites', {
      method: 'POST',
      body: { newsId: items[0].id },
      token: targetChanged.token,
    });
    check('解除后写操作恢复（C4）', afterChange.ids.includes(items[0].id));
  }

  // 12) 管理端：运营审计（P1-15）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const auditKey = '__contract_audit';
    try {
      // C10：清理也走受护端点，**必须**带 step-up 头。少了它，这个 DELETE 会被
      // 40304 挡下，而下面的 `catch { ignore }` 会把失败吞掉——症状是紧接着的
      // POST 报「该源标识已存在」，与真正的原因隔着两层。
      await request(`/admin/sources/${auditKey}`, { method: 'DELETE', token, headers: suHeaders });
    } catch {
      /* ignore：本就不存在 */
    }
    await request('/admin/sources', {
      method: 'POST',
      token,
      body: {
        key: auditKey,
        name: '契约审计源',
        channel: 'tech',
        url: 'https://example.com/audit.xml',
        weight: 4,
      },
    });
    await request(`/admin/sources/${auditKey}`, {
      method: 'PATCH',
      token,
      body: { weight: 6, enabled: false },
    });

    const page = await request('/admin/audit?limit=50', { token });
    check(
      'GET /admin/audit 返回 {list,hasMore,nextCursor}',
      Array.isArray(page.list) && typeof page.hasMore === 'boolean' && 'nextCursor' in page,
      `${page.list.length} 条`,
    );
    const acts = (page.list ?? []).map((a) => `${a.action}:${a.target}`);
    check('新增源留痕（source.create）', acts.includes(`source.create:${auditKey}`));
    check('修改源留痕（source.update）', acts.includes(`source.update:${auditKey}`));

    const upd = (page.list ?? []).find(
      (a) => a.action === 'source.update' && a.target === auditKey,
    );
    check(
      '更新记录含字段级 diff（旧→新）',
      !!upd?.detail?.changes &&
        Array.isArray(upd.detail.changes.weight) &&
        upd.detail.changes.weight[1] === 6,
      JSON.stringify(upd?.detail?.changes?.weight ?? null),
    );
    check(
      '审计行含操作者与时间',
      (page.list ?? []).every((a) => !!a.actorPhone && !!a.createdAt),
    );

    const filtered = await request(`/admin/audit?target=${auditKey}`, { token });
    check(
      'target 过滤只看该对象',
      (filtered.list ?? []).length > 0 && (filtered.list ?? []).every((a) => a.target === auditKey),
    );

    // 收尾
    await request(`/admin/sources/${auditKey}`, { method: 'DELETE', token, headers: suHeaders });
    const afterDelete = await request(`/admin/audit?target=${auditKey}`, { token });
    const acts2 = (afterDelete.list ?? []).map((a) => a.action);
    check('删除源留痕（source.delete）', acts2.includes('source.delete'));
  }

  // 13) 管理端：源健康历史与趋势（P1-19）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  //     列表回答"哪个源值得点进去"（health 摘要），单源回答"它到底怎么了"
  //     （summary 总览 / points 按天序列 / failures 失败原文）。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const listData = await request('/admin/sources', { token });
    check(
      'GET /admin/sources 附 healthDays（列表摘要窗口）',
      typeof listData.healthDays === 'number' && listData.healthDays > 0,
      `healthDays=${listData.healthDays}`,
    );
    check(
      '每行附 health 摘要（null 或 {attempts,rate,avgItems}）',
      (listData.list ?? []).every(
        (s) =>
          s.health === null ||
          (typeof s.health === 'object' &&
            typeof s.health.attempts === 'number' &&
            (s.health.rate === null || typeof s.health.rate === 'number')),
      ),
    );

    const firstKey = (listData.list ?? [])[0]?.key;
    check('列表非空可取到首个源', !!firstKey, String(firstKey));
    if (firstKey) {
      const trend = await request(`/admin/sources/${firstKey}/health`, { token });
      check(
        'GET /admin/sources/{key}/health 形状（key/days/keepDays/summary/points/failures）',
        trend.key === firstKey &&
          typeof trend.days === 'number' &&
          typeof trend.keepDays === 'number' &&
          !!trend.summary &&
          Array.isArray(trend.points) &&
          Array.isArray(trend.failures),
      );
      check(
        'summary 含 attempts/ok/failed/rate/avgItems',
        typeof trend.summary.attempts === 'number' &&
          typeof trend.summary.ok === 'number' &&
          typeof trend.summary.failed === 'number' &&
          (trend.summary.rate === null || typeof trend.summary.rate === 'number') &&
          (trend.summary.avgItems === null || typeof trend.summary.avgItems === 'number'),
      );
      check(
        'days 收敛到保留期内（window_days：超窗口只会得到一片空白）',
        trend.days <= trend.keepDays,
        `days=${trend.days} <= keepDays=${trend.keepDays}`,
      );
      check(
        'points 按天分桶且字段完整（缺天不补零，空白=没抓）',
        (trend.points ?? []).every(
          (p) =>
            typeof p.date === 'string' &&
            typeof p.total === 'number' &&
            typeof p.ok === 'number' &&
            typeof p.failed === 'number' &&
            typeof p.items === 'number',
        ),
      );
      check(
        'failures 只列失败且带性质（transient/permanent）',
        (trend.failures ?? []).every(
          (f) =>
            typeof f.at === 'string' &&
            typeof f.items === 'number' &&
            (f.kind === null || f.kind === 'transient' || f.kind === 'permanent'),
        ),
      );

      let zeroDays = false;
      try {
        await request(`/admin/sources/${firstKey}/health?days=0`, { token });
      } catch {
        zeroDays = true;
      }
      check('days=0 被拒（ge=1）', zeroDays);

      let bigDays = false;
      try {
        await request(`/admin/sources/${firstKey}/health?days=9999`, { token });
      } catch {
        bigDays = true;
      }
      check('days 越界被拒（le=365）', bigDays);
    }

    let missing = false;
    try {
      await request('/admin/sources/__no_such_source__/health', { token });
    } catch {
      missing = true;
    }
    check('未知源的趋势 404', missing);
  }

  // 13.5) 管理端：运营大盘（P1-76 / E7）
  //      默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  //      核心口径：单端点返回全部 5 个板块（少一个就是"页面有一块是空的"，
  //      而接口层看不出来）；days 越界在校验层被拒、超保留期被收敛；
  //      计数口径（可见/隐藏分开、待办只数 pending）由后端单测钉，这里只查形状。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const dash = await request('/admin/dashboard', { token });
    check(
      'GET /admin/dashboard 返回全部板块',
      dash.days > 0 &&
        typeof dash.generatedAt === 'string' &&
        !!dash.content &&
        !!dash.todo &&
        !!dash.engagement &&
        !!dash.sourceHealth &&
        !!dash.moderation &&
        !!dash.search,
      JSON.stringify(Object.keys(dash)),
    );
    check(
      'content 六项计数齐全（news/newsHidden/comments/users/sources/sourcesEnabled）',
      ['news', 'newsHidden', 'comments', 'users', 'sources', 'sourcesEnabled'].every(
        (k) => typeof dash.content[k] === 'number',
      ),
      JSON.stringify(dash.content),
    );
    check(
      'todo 三条队列（reports/commentReviews/nicknameReviews）',
      ['reports', 'commentReviews', 'nicknameReviews'].every(
        (k) => typeof dash.todo[k] === 'number',
      ),
      JSON.stringify(dash.todo),
    );
    check(
      'sourceHealth 含 windowDays/unhealthy/worst，worst 按失败率升序',
      typeof dash.sourceHealth.windowDays === 'number' &&
        typeof dash.sourceHealth.unhealthy === 'number' &&
        Array.isArray(dash.sourceHealth.worst) &&
        (dash.sourceHealth.worst ?? []).every(
          (w) =>
            typeof w.key === 'string' &&
            typeof w.attempts === 'number' &&
            typeof w.failed === 'number' &&
            (w.rate === null || typeof w.rate === 'number'),
        ) &&
        (dash.sourceHealth.worst ?? []).every(
          (w, i, a) => i === 0 || (a[i - 1].rate ?? 1) <= (w.rate ?? 1),
        ),
      `worst=${(dash.sourceHealth.worst ?? []).map((w) => w.key).join(',')}`,
    );
    check(
      'moderation 命中趋势按天升序且两序列齐全',
      Array.isArray(dash.moderation.hitsTrend) &&
        (dash.moderation.hitsTrend ?? []).every(
          (p, i, a) =>
            typeof p.date === 'string' &&
            typeof p.sensitive === 'number' &&
            typeof p.nickname === 'number' &&
            (i === 0 || a[i - 1].date < p.date),
        ),
      `trend=${(dash.moderation.hitsTrend ?? []).length} 天`,
    );
    const dash7 = await request('/admin/dashboard?days=7', { token });
    check('days 回显（7）', dash7.days === 7, String(dash7.days));
    const dashBig = await request('/admin/dashboard?days=90', { token });
    check('days 超保留期被收敛到 30', dashBig.days === 30, String(dashBig.days));

    let badDays = false;
    try {
      await request('/admin/dashboard?days=999', { token });
    } catch {
      badDays = true;
    }
    check('days 越界被拒（le=365）', badDays);
  }

  // 13.6) 管理端：举报复核队列（P1-77 / E5 前端补齐）
  //      默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  //      这条链路此前**完全没有契约覆盖**，而前端本轮开始依赖它（复核页），
  //      所以补上：形状 + 预览三件套 + 处置两端 + 游标翻页。
  //      收尾把本脚本自己造的那条举报处理掉，避免待办数越跑越大。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const reports = await request('/admin/reports?status=all&limit=5', { token });
    check(
      'GET /admin/reports 返回 { list, nextCursor }（无 total/hasMore）',
      Array.isArray(reports.list) && 'nextCursor' in reports,
      JSON.stringify(Object.keys(reports)),
    );
    check(
      '举报行含处置状态四件套',
      (reports.list ?? []).every(
        (r) =>
          typeof r.id === 'number' &&
          (r.targetType === 'comment' || r.targetType === 'news') &&
          typeof r.targetId === 'string' &&
          typeof r.reason === 'string' &&
          ['pending', 'resolved', 'dismissed'].includes(r.status) &&
          (r.resolution === null || r.resolution === 'hidden' || r.resolution === 'kept'),
      ),
      `共 ${(reports.list ?? []).length} 条`,
    );
    check(
      '举报行含被举报对象预览四件套（P1-77：复核页要看得见在举报什么）',
      (reports.list ?? []).every(
        (r) =>
          'targetTitle' in r &&
          'targetSnippet' in r &&
          'targetNewsId' in r &&
          typeof r.targetMissing === 'boolean' &&
          // 对象还在就必须给得出一个可跳转的资讯 id
          (r.targetMissing || typeof r.targetNewsId === 'string'),
      ),
      'targetTitle/targetSnippet/targetNewsId/targetMissing',
    );

    // 造一条 → 处置 → 校验两端；两条分支（hidden / kept）各测一次
    const pendingReports = (reports.list ?? []).filter((r) => r.status === 'pending');
    if (pendingReports.length) {
      const target = pendingReports[0];
      const resolved = await request(`/admin/reports/${target.id}/resolve`, {
        method: 'POST',
        token,
        body: { resolution: 'kept' },
      });
      check(
        '处置返回与列表同形状，且已带预览（前端可就地更新行）',
        resolved.id === target.id &&
          resolved.status === 'dismissed' &&
          resolved.resolution === 'kept' &&
          'targetMissing' in resolved,
        `${resolved.status}/${resolved.resolution}`,
      );
    }

    let badResolution = false;
    try {
      await request('/admin/reports/99999999/resolve', {
        method: 'POST',
        token,
        body: { resolution: 'kept' },
      });
    } catch {
      badResolution = true;
    }
    check('处置不存在的举报 404', badResolution);
  }

  // 14) 管理端：资讯审核与下架（P1-20）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  //     核心口径：软下架可恢复、公开四路实时过滤（对外与不存在同码 40400）、
  //     理由必填 422、操作幂等（重复下架不抹掉第一次理由）、字段级 diff 审计。
  //     收尾必须恢复演示条，避免污染开发库（与 12 的临时源同思路）。
  if (process.env.CHECK_ADMIN === '1' && token) {
    // 14.1 列表形状与筛选
    const allNews = await request('/admin/news?limit=100', { token });
    check(
      'GET /admin/news 返回 {list,total,hasMore,nextCursor}',
      Array.isArray(allNews.list) &&
        typeof allNews.total === 'number' &&
        typeof allNews.hasMore === 'boolean' &&
        'nextCursor' in allNews,
      `${allNews.total} 条`,
    );
    check(
      '管理端行含 hidden 三件套与 publishedAt/hotScore',
      (allNews.list ?? []).every(
        (n) =>
          typeof n.hidden === 'boolean' &&
          'hiddenAt' in n &&
          'hiddenReason' in n &&
          typeof n.hotScore === 'number' &&
          (n.publishedAt === null || typeof n.publishedAt === 'string'),
      ),
    );

    let badStatus = false;
    try {
      await request('/admin/news?status=bogus', { token });
    } catch {
      badStatus = true;
    }
    check('非法 status 被拒（422）', badStatus);

    const visibleNews = await request('/admin/news?status=visible', { token });
    check(
      'status=visible 只返回可见行',
      (visibleNews.list ?? []).every((n) => n.hidden === false),
    );
    const hiddenNews = await request('/admin/news?status=hidden', { token });
    check(
      'status=hidden 只返回已下架行',
      (hiddenNews.list ?? []).every((n) => n.hidden === true),
    );

    let missingNews = false;
    try {
      await request('/admin/news/__no_such_news__', { token });
    } catch {
      missingNews = true;
    }
    check('未知资讯 404', missingNews);

    // 14.2 挑一条可见资讯走完整「下架 → 恢复」闭环
    const victim = (visibleNews.list ?? []).find((n) => n.id);
    check('取到一条可见资讯作为演示条', !!victim, victim?.title);
    if (victim) {
      // 审计计数只数**本次运行新增**：持久库（PG）上审计日志跨运行累积
      // （P1-20 首跑就留过 1 hide + 1 restore + 2 update，mock 端因审计日志
      // 在内存、重启清零，永远暴露不了这个问题——绝对计数必挂）。用「id 大于
      // 快照最新 id」划窗口：审计列表按 id 倒序，本次新条目恒在最前，既不怕
      // 历史累积，也不怕 limit 截断（截掉的都是更旧条目）。
      const auditSnap = await request(`/admin/audit?target=${victim.id}&limit=100`, { token });
      const baseAuditId = auditSnap.list?.[0]?.id ?? 0;

      // 下架前公开可访问（恢复后要能原样回来）
      const pubBefore = await request(`/news/${victim.id}`);
      check('下架前公开详情可访问', pubBefore.id === victim.id);

      let emptyReason = false;
      try {
        await request(`/admin/news/${victim.id}/hide`, {
          method: 'POST',
          token,
          body: { reason: '   ' },
        });
      } catch {
        emptyReason = true;
      }
      check('纯空白理由被拒（422）', emptyReason);

      let noReason = false;
      try {
        await request(`/admin/news/${victim.id}/hide`, { method: 'POST', token, body: {} });
      } catch {
        noReason = true;
      }
      check('缺 reason 字段被拒（422）', noReason);

      const hiddenOne = await request(`/admin/news/${victim.id}/hide`, {
        method: 'POST',
        token,
        body: { reason: '契约检查下架理由' },
      });
      check(
        '下架成功：hidden=true 且带回填理由',
        hiddenOne.hidden === true && hiddenOne.hiddenReason === '契约检查下架理由',
      );

      // 公开四路实时消失（后审核模式：过滤条件即时生效，无需等下一轮抓取）
      let pubDetailGone = false;
      try {
        await request(`/news/${victim.id}`);
      } catch (e) {
        pubDetailGone = e.message === '内容不存在或已下架';
      }
      check('下架后公开详情 404（与不存在同码同文案）', pubDetailGone);

      const pubListAfter = await request(`/news?channel=${victim.channel}&pageSize=100`);
      check('下架后公开列表不出现', !(pubListAfter.list ?? []).some((n) => n.id === victim.id));

      const pubSearchAfter = await request(
        `/news/search?q=${encodeURIComponent(victim.title.slice(0, 6))}`,
      );
      check('下架后搜索不出现', !(pubSearchAfter.list ?? []).some((n) => n.id === victim.id));

      let commentsGone = false;
      try {
        await request(`/news/${victim.id}/comments`, { token });
      } catch {
        commentsGone = true;
      }
      check('下架后评论列表 404', commentsGone);

      // 幂等：重复下架不覆盖原理由、不重复记审计
      const again = await request(`/admin/news/${victim.id}/hide`, {
        method: 'POST',
        token,
        body: { reason: '第二个理由' },
      });
      check('重复下架不覆盖原理由', again.hiddenReason === '契约检查下架理由');

      const auditHide = await request(`/admin/audit?target=${victim.id}&limit=100`, { token });
      const hides = (auditHide.list ?? []).filter(
        (a) => a.action === 'news.hide' && a.id > baseAuditId,
      );
      check('重复下架只记一条审计', hides.length === 1, `${hides.length} 条`);
      check(
        '下架审计含理由与标题（运营认标题不认 id）',
        hides[0]?.detail?.reason === '契约检查下架理由' && hides[0]?.detail?.title === victim.title,
      );

      // 恢复：清空理由/时间，公开查询实时回来
      const restored = await request(`/admin/news/${victim.id}/restore`, {
        method: 'POST',
        token,
      });
      check(
        '恢复后 hidden=false 且理由清空',
        restored.hidden === false && restored.hiddenReason === null,
      );
      const pubBack = await request(`/news/${victim.id}`);
      check('恢复后公开详情实时可见', pubBack.id === victim.id);

      const restoredAgain = await request(`/admin/news/${victim.id}/restore`, {
        method: 'POST',
        token,
      });
      check('重复恢复不报错', restoredAgain.hidden === false);
      const auditRestore = await request(`/admin/audit?target=${victim.id}&limit=100`, { token });
      check(
        '恢复只记一条审计',
        (auditRestore.list ?? []).filter((a) => a.action === 'news.restore' && a.id > baseAuditId)
          .length === 1,
      );

      // 置顶：驼峰生效、蛇形被忽略、无变化不记审计。
      // 先翻到反值再翻回来——不管演示条初始是不是置顶，都必然产生两次变化
      const startTop = victim.isTop === true;
      const flip1 = await request(`/admin/news/${victim.id}`, {
        method: 'PATCH',
        token,
        body: { isTop: !startTop },
      });
      check('PATCH 置顶切换生效（驼峰 isTop）', flip1.isTop === !startTop);

      const snake = await request(`/admin/news/${victim.id}`, {
        method: 'PATCH',
        token,
        body: { is_top: !startTop },
      });
      check('蛇形 is_top 被静默忽略（多词字段驼峰约定）', snake.isTop === !startTop);

      const flip2 = await request(`/admin/news/${victim.id}`, {
        method: 'PATCH',
        token,
        body: { isTop: startTop },
      });
      check('PATCH 切回原值生效', flip2.isTop === startTop);

      const auditTop = await request(`/admin/audit?target=${victim.id}&limit=100`, { token });
      const updates = (auditTop.list ?? []).filter(
        (a) => a.action === 'news.update' && a.id > baseAuditId,
      );
      check('两次变化只记两条 news.update', updates.length === 2, `${updates.length} 条`);
      check(
        'update 审计含字段级 diff 与标题',
        Array.isArray(updates[0]?.detail?.changes?.isTop) &&
          updates[0].detail.changes.isTop[1] === startTop &&
          updates[0]?.detail?.title === victim.title,
        JSON.stringify(updates[0]?.detail?.changes ?? null),
      );

      // 管理端关键词：标题/摘要命中，且**含已下架行**（默认 all 的口径）
      const kwPage = await request(
        `/admin/news?keyword=${encodeURIComponent(victim.title.slice(0, 6))}`,
        { token },
      );
      check(
        '管理端关键词搜索命中演示条',
        (kwPage.list ?? []).some((n) => n.id === victim.id),
      );
    }

    // 14.3 游标分页：不重不漏 + 坏游标 400
    const p1 = await request('/admin/news?limit=2', { token });
    check('第一页满页且有 nextCursor', (p1.list ?? []).length === 2 && !!p1.nextCursor);
    if (p1.nextCursor) {
      const p2 = await request(`/admin/news?limit=2&cursor=${encodeURIComponent(p1.nextCursor)}`, {
        token,
      });
      const ids1 = (p1.list ?? []).map((n) => n.id);
      const ids2 = (p2.list ?? []).map((n) => n.id);
      check(
        '游标翻页不重不漏',
        ids2.length > 0 && ids2.every((id) => !ids1.includes(id)),
        `第一页 ${ids1.length} / 第二页 ${ids2.length}`,
      );
    }
    let badCursor = false;
    try {
      await request('/admin/news?cursor=!!!not-base64!!!', { token });
    } catch {
      badCursor = true;
    }
    check('坏游标被拒（400）', badCursor);
  }

  // 15) 管理端：评论治理（P1-22）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  //     核心口径：软隐藏可恢复、公开五路实时过滤（主列表/回复/计数/不可点赞/
  //     不可回复）、理由必填 422、幂等（重复隐藏不抹掉第一次理由）、40405 与
  //     公开端 40401 刻意分开、审计留痕（comment.hide/restore）。
  //     演示评论自建自销（隐藏→恢复→删除），不污染种子数据；审计计数用
  //     id 窗口法（P1-21 修法）：持久库（PG）上审计日志跨运行累积，
  //     绝对计数必挂——「id 大于快照最新 id」划窗口，既不怕历史累积，
  //     也不怕 limit 截断（截掉的都是更旧条目）。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const DEMO_NEWS = 'n4'; // 与 6/8.1 同一篇演示资讯

    // 15.1 列表形状与筛选
    const allComments = await request('/admin/comments?limit=100', { token });
    check(
      'GET /admin/comments 返回 {list,total,hasMore,nextCursor}',
      Array.isArray(allComments.list) &&
        typeof allComments.total === 'number' &&
        typeof allComments.hasMore === 'boolean' &&
        'nextCursor' in allComments,
      `${allComments.total} 条`,
    );
    check(
      '管理端行含 hidden 三件套与 newsTitle/userName/replyCount，已软删除不返回',
      (allComments.list ?? []).every(
        (c) =>
          typeof c.hidden === 'boolean' &&
          'hiddenAt' in c &&
          'hiddenReason' in c &&
          typeof c.newsId === 'string' &&
          typeof c.userName === 'string' &&
          typeof c.replyCount === 'number' &&
          c.isDeleted === false,
      ),
    );

    let badStatus = false;
    try {
      await request('/admin/comments?status=bogus', { token });
    } catch {
      badStatus = true;
    }
    check('非法 status 被拒（422）', badStatus);

    const visibleComments = await request('/admin/comments?status=visible&limit=100', { token });
    check(
      'status=visible 只返回可见行',
      (visibleComments.list ?? []).every((c) => c.hidden === false),
    );
    const hiddenComments = await request('/admin/comments?status=hidden&limit=100', { token });
    check(
      'status=hidden 只返回已隐藏行',
      (hiddenComments.list ?? []).every((c) => c.hidden === true),
    );

    let noToken = false;
    try {
      await request('/admin/comments');
    } catch (e) {
      noToken = e.message === '未登录或登录已过期';
    }
    check('无 token 访问 /admin/comments → 401', noToken);

    // 15.2 自建演示评论（主 + 回复），走完整「隐藏 → 恢复」闭环
    const demoRoot = await request(`/news/${DEMO_NEWS}/comments`, {
      method: 'POST',
      body: { content: '契约检查治理演示主评论' },
      token,
    });
    const demoReply = await request(`/news/${DEMO_NEWS}/comments`, {
      method: 'POST',
      body: { content: '契约检查治理演示回复', parentId: demoRoot.id },
      token,
    });
    check(
      '演示主评论与回复创建成功（rootId 拍平到主评论）',
      !!demoRoot.id && !!demoReply.id && demoReply.rootId === demoRoot.id,
    );

    const auditSnap = await request(`/admin/audit?target=${demoRoot.id}&limit=100`, { token });
    const baseAuditId = auditSnap.list?.[0]?.id ?? 0;

    // 隐藏前：公开可见、计数含演示主评论、公开输出不暴露隐藏字段
    const pubBefore = await request(`/news/${DEMO_NEWS}/comments`, { token });
    const rootBefore = (pubBefore.list ?? []).find((c) => c.id === demoRoot.id);
    check(
      '隐藏前主评论公开可见且带回复数',
      !!rootBefore && (rootBefore.replyCount ?? 0) >= 1,
      String(rootBefore?.replyCount),
    );
    check(
      '隐藏前回复列表可见',
      ((await request(`/news/${DEMO_NEWS}/comments/${demoRoot.id}/replies`, { token })).list ?? [])
        .length === 1,
    );
    check(
      '公开输出不暴露 hidden 字段（不该被读到"它被隐藏了"）',
      !!rootBefore && !('hidden' in rootBefore) && !('hiddenReason' in rootBefore),
    );

    // 计数联动基线：隐藏 -1 / 恢复 +1（mock 的 POST 不动 commentCount，
    // 真实后端 POST +1——两种口径下"相对基线"的断言都成立）
    const newsBefore = await request(`/news/${DEMO_NEWS}`);
    check('详情 commentCount 为数字', typeof newsBefore.commentCount === 'number');

    // 15.3 理由必填（422 三态：纯空白 / 缺字段 / 超长）
    let emptyReason = false;
    try {
      await request(`/admin/comments/${demoRoot.id}/hide`, {
        method: 'POST',
        token,
        body: { reason: '   ' },
      });
    } catch {
      emptyReason = true;
    }
    check('纯空白理由被拒（422）', emptyReason);

    let noReason = false;
    try {
      await request(`/admin/comments/${demoRoot.id}/hide`, { method: 'POST', token, body: {} });
    } catch {
      noReason = true;
    }
    check('缺 reason 字段被拒（422）', noReason);

    let tooLong = false;
    try {
      await request(`/admin/comments/${demoRoot.id}/hide`, {
        method: 'POST',
        token,
        body: { reason: '长'.repeat(301) },
      });
    } catch {
      tooLong = true;
    }
    check('超长理由被拒（422）', tooLong);

    const hiddenRoot = await request(`/admin/comments/${demoRoot.id}/hide`, {
      method: 'POST',
      token,
      body: { reason: '契约检查隐藏理由' },
    });
    check(
      '隐藏成功：hidden=true 且带回填理由',
      hiddenRoot.hidden === true && hiddenRoot.hiddenReason === '契约检查隐藏理由',
    );

    // 15.4 公开五路实时消失（过滤条件即时生效，无需等任何定时任务）
    const pubAfter = await request(`/news/${DEMO_NEWS}/comments`, { token });
    check('隐藏后主列表不出现', !(pubAfter.list ?? []).some((c) => c.id === demoRoot.id));
    const repAfter = await request(`/news/${DEMO_NEWS}/comments/${demoRoot.id}/replies`, { token });
    check('隐藏后回复列表为空（失去语境的回复不裸露）', (repAfter.list ?? []).length === 0);
    const newsHidden = await request(`/news/${DEMO_NEWS}`);
    check(
      '隐藏后详情计数 -1',
      newsHidden.commentCount === (newsBefore.commentCount ?? 0) - 1,
      `${newsBefore.commentCount} → ${newsHidden.commentCount}`,
    );
    let likeGone = false;
    try {
      await request(`/news/${DEMO_NEWS}/comments/${demoRoot.id}`, {
        method: 'PATCH',
        body: { liked: true },
        token,
      });
    } catch (e) {
      likeGone = e.message === '评论不存在';
    }
    check('隐藏后点赞 40401（与不存在同文案）', likeGone);
    let replyGone = false;
    try {
      await request(`/news/${DEMO_NEWS}/comments`, {
        method: 'POST',
        body: { content: '对已隐藏评论盖楼', parentId: demoRoot.id },
        token,
      });
    } catch (e) {
      replyGone = e.message === '回复的评论不存在';
    }
    check('隐藏后回复 40003', replyGone);

    // 15.5 幂等 + 审计（id 窗口法）
    const again = await request(`/admin/comments/${demoRoot.id}/hide`, {
      method: 'POST',
      token,
      body: { reason: '第二个理由' },
    });
    check('重复隐藏不覆盖原理由', again.hiddenReason === '契约检查隐藏理由');

    const auditHide = await request(`/admin/audit?target=${demoRoot.id}&limit=100`, { token });
    const hides = (auditHide.list ?? []).filter(
      (a) => a.action === 'comment.hide' && a.id > baseAuditId,
    );
    check('重复隐藏只记一条审计', hides.length === 1, `${hides.length} 条`);
    check(
      'hide 审计含理由/内容片段/资讯标题/作者名（运营认内容不认 id）',
      hides[0]?.detail?.reason === '契约检查隐藏理由' &&
        typeof hides[0]?.detail?.content === 'string' &&
        typeof hides[0]?.detail?.newsTitle === 'string' &&
        typeof hides[0]?.detail?.userName === 'string',
      JSON.stringify(hides[0]?.detail ?? null),
    );

    // 15.6 恢复：清空理由/时间，公开五路实时回来
    const restored = await request(`/admin/comments/${demoRoot.id}/restore`, {
      method: 'POST',
      token,
    });
    check(
      '恢复后 hidden=false 且理由清空',
      restored.hidden === false && restored.hiddenReason === null,
    );
    const pubBack = await request(`/news/${DEMO_NEWS}/comments`, { token });
    check(
      '恢复后主评论公开可见',
      (pubBack.list ?? []).some((c) => c.id === demoRoot.id),
    );
    const repBack = await request(`/news/${DEMO_NEWS}/comments/${demoRoot.id}/replies`, { token });
    check('恢复后回复列表回来', (repBack.list ?? []).length === 1);
    const newsBack = await request(`/news/${DEMO_NEWS}`);
    check(
      '恢复后详情计数回基线',
      newsBack.commentCount === newsBefore.commentCount,
      `${newsHidden.commentCount} → ${newsBack.commentCount}`,
    );

    const restoredAgain = await request(`/admin/comments/${demoRoot.id}/restore`, {
      method: 'POST',
      token,
    });
    check('重复恢复不报错', restoredAgain.hidden === false);
    const auditRestore = await request(`/admin/audit?target=${demoRoot.id}&limit=100`, { token });
    check(
      '恢复只记一条审计',
      (auditRestore.list ?? []).filter((a) => a.action === 'comment.restore' && a.id > baseAuditId)
        .length === 1,
    );

    // 15.7 管理端筛选：关键词 / 按资讯（驼峰）/ 蛇形被静默忽略
    const kwPage = await request(
      `/admin/comments?keyword=${encodeURIComponent('契约检查治理演示主评论')}`,
      { token },
    );
    check(
      '管理端关键词搜索命中演示评论',
      (kwPage.list ?? []).some((c) => c.id === demoRoot.id),
    );

    const byNews = await request(`/admin/comments?newsId=${DEMO_NEWS}&limit=100`, { token });
    check(
      'newsId（驼峰）按资讯过滤命中',
      (byNews.list ?? []).some((c) => c.id === demoRoot.id) &&
        (byNews.list ?? []).every((c) => c.newsId === DEMO_NEWS),
    );
    // 多词查询参数驼峰约定（与 P1-18 respectRobots 同款缝）：蛇形不被 FastAPI
    // 报错，只是**静默不过滤**——前后端各自单测都发现不了，只在契约层焊死
    const noFilter = await request('/admin/comments?limit=1', { token });
    const snake = await request(`/admin/comments?news_id=${DEMO_NEWS}&limit=100`, { token });
    check(
      '蛇形 news_id 被静默忽略（不过滤也不报错）',
      snake.total === noFilter.total,
      `蛇形 ${snake.total} vs 不过滤 ${noFilter.total}`,
    );

    // 15.8 40405：管理端视角的「不存在」与公开端 40401 刻意分开
    let missingHide = false;
    try {
      await request('/admin/comments/__no_such_comment__/hide', {
        method: 'POST',
        token,
        body: { reason: 'x' },
      });
    } catch (e) {
      missingHide = e.message === '评论不存在或已删除';
    }
    check('隐藏未知评论 40405', missingHide);

    let missingRestore = false;
    try {
      await request('/admin/comments/__no_such_comment__/restore', { method: 'POST', token });
    } catch (e) {
      missingRestore = e.message === '评论不存在或已删除';
    }
    check('恢复未知评论 40405', missingRestore);

    const one = await request(`/admin/comments/${demoRoot.id}`, { token });
    check(
      '管理端详情与列表行同构（含资讯标题）',
      one.id === demoRoot.id && typeof one.newsTitle === 'string' && 'hiddenReason' in one,
      one.newsTitle,
    );

    // 15.9 游标分页：不重不漏（明文 id 游标；锚点找不到时静默忽略不报错，
    //      与后端 db.get 未命中即跳过的口径一致——这里只验正常翻页）
    const p1 = await request('/admin/comments?limit=2', { token });
    check('第一页满页且有 nextCursor', (p1.list ?? []).length === 2 && !!p1.nextCursor);
    if (p1.nextCursor) {
      const p2 = await request(
        `/admin/comments?limit=2&cursor=${encodeURIComponent(p1.nextCursor)}`,
        { token },
      );
      const ids1 = (p1.list ?? []).map((c) => c.id);
      const ids2 = (p2.list ?? []).map((c) => c.id);
      check(
        '游标翻页不重不漏',
        ids2.length > 0 && ids2.every((id) => !ids1.includes(id)),
        `第一页 ${ids1.length} / 第二页 ${ids2.length}`,
      );
    }

    // 15.10 收尾：删除演示评论（作者侧），治理视图随之不再返回
    await request(`/news/${DEMO_NEWS}/comments/${demoRoot.id}`, { method: 'DELETE', token });
    await request(`/news/${DEMO_NEWS}/comments/${demoReply.id}`, { method: 'DELETE', token });
    let goneAfterDelete = false;
    try {
      await request(`/admin/comments/${demoRoot.id}`, { token });
    } catch {
      goneAfterDelete = true;
    }
    check('删除后治理视图不再返回（40405）', goneAfterDelete);
  }

  // 16) 敏感词自动审核（P1-23）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  //     核心口径：服务端唯一写入口检测；三种动作（block 拒绝 / mask 脱敏+待复核 /
  //     allow 豁免）；归一化吃掉大小写·全半角·空格标点零宽干扰；改词**立即生效**；
  //     命中留痕（含被拦截未落库的）。收尾删掉临时词与演示评论，不污染开发库。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const DEMO_NEWS = 'n4'; // 与 6/15 同一篇演示资讯
    // 四个临时词刻意**互不为子串**：否则「短词」会顺带命中别的用例的文本，
    // 让"停用后应当放行"这类断言莫名其妙变红（看起来像回归，其实是种子设计问题）
    const W_BLOCK = '契约拦截词';
    const W_MASK = '契约脱敏词';
    const W_SUB = '契约检查'; // 短词：会误杀「契约检查子词」
    const W_ALLOW = '契约检查子词'; // 白名单：把上面那条豁免掉

    // 16.1 词库列表形状与筛选
    const allWords = await request('/admin/words?limit=500', { token });
    check(
      'GET /admin/words 返回 {list,total}',
      Array.isArray(allWords.list) && typeof allWords.total === 'number',
      `${allWords.total} 条`,
    );
    check(
      '词行含 word/action/category/enabled/hitCount 与时间戳',
      (allWords.list ?? []).every(
        (w) =>
          typeof w.word === 'string' &&
          ['block', 'mask', 'allow'].includes(w.action) &&
          typeof w.enabled === 'boolean' &&
          typeof w.hitCount === 'number' &&
          typeof w.updatedAt === 'string',
      ),
    );

    let badWordAction = false;
    try {
      await request('/admin/words?action=bogus', { token });
    } catch {
      badWordAction = true;
    }
    check('非法 action 筛选被拒（422）', badWordAction);

    // 16.2 新增：太短 42202 / 重复 40905 / 正常新增
    const beforeAdd = allWords.total ?? 0;
    const wBlock = await request('/admin/words', {
      method: 'POST',
      token,
      body: { word: W_BLOCK, action: 'block', category: '契约检查' },
    });
    check(
      'POST /admin/words 新增成功（block）',
      wBlock.word === W_BLOCK && wBlock.action === 'block',
    );
    check(
      '新增后总数 +1',
      (await request('/admin/words?limit=500', { token })).total === beforeAdd + 1,
    );

    let dupWord = false;
    try {
      await request('/admin/words', {
        method: 'POST',
        token,
        body: { word: W_BLOCK, action: 'mask' },
      });
    } catch {
      dupWord = true;
    }
    check('重复词被拒（40905）', dupWord);

    let shortWord = '';
    try {
      // 归一化后只剩 1 个有效字符：空格与标点不计入长度（1 字词在正常表达里
      // 遍地都是，挡在入库这一步比事后救火便宜）
      await request('/admin/words', { method: 'POST', token, body: { word: '契!' } });
    } catch (e) {
      shortWord = e.message;
    }
    check(
      '归一化后不足 2 字被拒（42202）且文案说明"空格与标点不计"',
      shortWord.includes('有效字符'),
      shortWord,
    );

    const wMask = await request('/admin/words', {
      method: 'POST',
      token,
      body: { word: W_MASK, action: 'mask', category: '契约检查' },
    });
    const wSub = await request('/admin/words', {
      method: 'POST',
      token,
      body: { word: W_SUB, action: 'mask', category: '契约检查' },
    });
    const wAllow = await request('/admin/words', {
      method: 'POST',
      token,
      body: { word: W_ALLOW, action: 'allow', category: '契约检查' },
    });
    check('mask / allow 词均可入库', wMask.action === 'mask' && wAllow.action === 'allow');

    // 16.3 命中日志基线：只数**本次运行新增**（持久库跨运行累积，绝对计数必挂）
    const hitSnap = await request('/admin/moderation/hits?limit=1', { token });
    const baseHitId = (hitSnap.list ?? [])[0]?.id ?? 0;

    // 16.4 block：命中即拒绝提交，且**不留评论**
    let blocked = false;
    let blockMsg = '';
    try {
      await request(`/news/${DEMO_NEWS}/comments`, {
        method: 'POST',
        token,
        body: { content: `这是一条${W_BLOCK}测试` },
      });
    } catch (e) {
      blocked = true;
      blockMsg = e.message;
    }
    check('block 词命中 → 提交被拒', blocked, blockMsg);
    check(
      'block 提示不回显命中词（回显=给绕过者对照表）',
      blocked && !blockMsg.includes(W_BLOCK),
      blockMsg,
    );

    // 绕过：空格 / 标点 / 全角隔断——归一化后与原文同形，必须照样拦住
    const variants = [
      `这是一条${W_BLOCK.slice(0, 2)} ${W_BLOCK.slice(2)}测试`, // 插空格
      `这是一条${W_BLOCK.slice(0, 2)}-${W_BLOCK.slice(2)}测试`, // 插标点
      `这是一条${W_BLOCK.slice(0, 2)}\u3000${W_BLOCK.slice(2)}测试`, // 全角空格
      `这是一条${W_BLOCK.slice(0, 2)}\u200B${W_BLOCK.slice(2)}测试`, // 零宽字符
    ];
    let bypassBlocked = 0;
    for (const v of variants) {
      try {
        await request(`/news/${DEMO_NEWS}/comments`, {
          method: 'POST',
          token,
          body: { content: v },
        });
      } catch {
        bypassBlocked += 1;
      }
    }
    check(
      `空格/标点/全角/零宽隔断均被拦截（归一化生效）`,
      bypassBlocked === variants.length,
      `${bypassBlocked}/${variants.length}`,
    );

    const hitsAfterBlock = await request(
      `/admin/moderation/hits?word=${encodeURIComponent(W_BLOCK)}&limit=100`,
      { token },
    );
    check(
      '被拦截也留痕（拦截不能是黑箱）',
      (hitsAfterBlock.list ?? []).filter((h) => h.id > baseHitId).length >= 5,
      `${(hitsAfterBlock.list ?? []).filter((h) => h.id > baseHitId).length} 条`,
    );
    check(
      '拦截的命中行 action=block 且 commentId 为 null（未落库）',
      (hitsAfterBlock.list ?? [])
        .filter((h) => h.id > baseHitId)
        .every((h) => h.action === 'block' && h.commentId === null),
    );

    // 16.5 mask：脱敏后发布 + 置为待复核
    const maskedComment = await request(`/news/${DEMO_NEWS}/comments`, {
      method: 'POST',
      token,
      body: { content: `这是一条${W_MASK}测试` },
    });
    const star = '*'.repeat(W_MASK.length);
    check(
      'mask 命中 → 正文被脱敏（按**词长**替换，不是区间长度）',
      maskedComment.content === `这是一条${star}测试`,
      maskedComment.content,
    );
    check('mask 命中 → pendingReview=true', maskedComment.pendingReview === true);
    check(
      'mask 命中 → 评论照常可见（脱敏不是下架）',
      (await request(`/news/${DEMO_NEWS}/comments`, { token })).list.some(
        (c) => c.id === maskedComment.id,
      ),
    );

    const hitsAfterMask = await request(
      `/admin/moderation/hits?word=${encodeURIComponent(W_MASK)}&limit=100`,
      { token },
    );
    const maskHit = (hitsAfterMask.list ?? []).find((h) => h.id > baseHitId);
    check(
      'mask 命中留痕：field=content、commentId 指向该评论、snippet 是原文',
      !!maskHit &&
        maskHit.field === 'content' &&
        maskHit.commentId === maskedComment.id &&
        maskHit.snippet.includes(W_MASK),
      maskHit?.snippet,
    );
    check(
      '脱敏后原文只留在命中日志里（snippet 是唯一证据）',
      !!maskHit && !maskedComment.content.includes(W_MASK),
    );

    // 16.6 allow 白名单：短词的误杀靠它豁免
    const allowedComment = await request(`/news/${DEMO_NEWS}/comments`, {
      method: 'POST',
      token,
      body: { content: `这是${W_ALLOW}测试` },
    });
    check(
      'allow 白名单豁免被包裹的短词命中',
      allowedComment.content === `这是${W_ALLOW}测试` && allowedComment.pendingReview === false,
      allowedComment.content,
    );
    const allowHits = await request(
      `/admin/moderation/hits?word=${encodeURIComponent(W_SUB)}&limit=100`,
      { token },
    );
    check(
      '被豁免的命中不写日志（白名单不是"命中了但放行"）',
      (allowHits.list ?? []).filter((h) => h.id > baseHitId).length === 0,
    );
    // 短词单独出现仍要命中（白名单只豁免指定片段）
    const subComment = await request(`/news/${DEMO_NEWS}/comments`, {
      method: 'POST',
      token,
      body: { content: `这是${W_SUB}之外的说法` },
    });
    check(
      '短词单独出现仍命中（白名单只豁免指定片段）',
      subComment.pendingReview === true,
      subComment.content,
    );

    // 16.7 热更新：停用后**立即**不再命中（改词即失效本进程缓存）
    await request(`/admin/words/${wBlock.id}`, {
      method: 'PATCH',
      token,
      body: { enabled: false },
    });
    const afterDisable = await request(`/news/${DEMO_NEWS}/comments`, {
      method: 'POST',
      token,
      body: { content: `停用后这条${W_BLOCK}应当放行` },
    });
    check(
      '停用词立即生效（热更新，无需重启）',
      afterDisable.content === `停用后这条${W_BLOCK}应当放行`,
      afterDisable.content,
    );
    // 词库刻意不提供单条详情（列表 + 增删改已够用，省一个端点就少一处漂移）
    const disabledRow = (await request('/admin/words?limit=500', { token })).list.find(
      (w) => w.id === wBlock.id,
    );
    check(
      '停用后列表里该词 enabled=false',
      disabledRow?.enabled === false,
      String(disabledRow?.enabled),
    );

    // 重新启用后再次命中：证明上面那条不是"碰巧没匹配"
    await request(`/admin/words/${wBlock.id}`, { method: 'PATCH', token, body: { enabled: true } });
    let reBlocked = false;
    try {
      await request(`/news/${DEMO_NEWS}/comments`, {
        method: 'POST',
        token,
        body: { content: `重新启用后${W_BLOCK}` },
      });
    } catch {
      reBlocked = true;
    }
    check('重新启用后再次命中（启停双向生效）', reBlocked);

    const reloaded = await request('/admin/words/reload', { method: 'POST', token });
    check(
      'POST /admin/words/reload 返回生效条数',
      typeof reloaded.count === 'number' && reloaded.count > 0,
      String(reloaded.count),
    );

    // 16.8 命中日志：形状、筛选、游标分页（只读）
    const hitPage = await request('/admin/moderation/hits?limit=20', { token });
    check(
      'GET /admin/moderation/hits 返回 {list,total,hasMore,nextCursor}',
      Array.isArray(hitPage.list) &&
        typeof hitPage.total === 'number' &&
        typeof hitPage.hasMore === 'boolean' &&
        'nextCursor' in hitPage,
      `${hitPage.total} 条`,
    );
    check(
      '命中行含 word/action/field/commentId/snippet/userName/createdAt',
      (hitPage.list ?? []).every(
        (h) =>
          typeof h.word === 'string' &&
          ['block', 'mask'].includes(h.action) &&
          ['content', 'name'].includes(h.field) &&
          'commentId' in h &&
          typeof h.snippet === 'string' &&
          typeof h.createdAt === 'string',
      ),
    );
    const byField = await request('/admin/moderation/hits?field=content&limit=100', { token });
    check(
      'field=content 只返回正文命中',
      (byField.list ?? []).every((h) => h.field === 'content'),
    );
    const byAction = await request('/admin/moderation/hits?action=block&limit=100', { token });
    check(
      'action=block 只返回拦截命中',
      (byAction.list ?? []).every((h) => h.action === 'block'),
    );
    let badField = false;
    try {
      await request('/admin/moderation/hits?field=bogus', { token });
    } catch {
      badField = true;
    }
    check('非法 field 被拒（422）', badField);

    const hp1 = await request('/admin/moderation/hits?limit=2', { token });
    if (hp1.nextCursor) {
      const hp2 = await request(`/admin/moderation/hits?limit=2&cursor=${hp1.nextCursor}`, {
        token,
      });
      const ids1 = (hp1.list ?? []).map((h) => h.id);
      check(
        '命中日志游标翻页不重复',
        (hp2.list ?? []).every((h) => !ids1.includes(h.id)),
      );
    }

    // 16.9 人工复核：幂等、只清标记不改可见性
    const pendingPage = await request('/admin/comments?status=pending&limit=100', { token });
    check(
      'status=pending 只返回待复核行',
      (pendingPage.list ?? []).every((c) => c.pendingReview === true),
      `${pendingPage.total} 条`,
    );
    check(
      '刚脱敏的评论出现在待复核列表',
      (pendingPage.list ?? []).some((c) => c.id === maskedComment.id),
    );

    const reviewed = await request(`/admin/comments/${maskedComment.id}/review`, {
      method: 'POST',
      token,
    });
    check(
      '复核后 pendingReview=false 且写入 reviewedAt',
      reviewed.pendingReview === false && !!reviewed.reviewedAt,
      String(reviewed.reviewedAt),
    );
    check(
      '复核不改可见性（它只是"人看过了"）',
      reviewed.hidden === false &&
        (await request(`/news/${DEMO_NEWS}/comments`, { token })).list.some(
          (c) => c.id === maskedComment.id,
        ),
    );
    const reviewedAgain = await request(`/admin/comments/${maskedComment.id}/review`, {
      method: 'POST',
      token,
    });
    check('复核幂等（重复调用不报错）', reviewedAgain.pendingReview === false);
    const pendingAfter = await request('/admin/comments?status=pending&limit=100', { token });
    check(
      '复核后从待复核列表消失',
      !(pendingAfter.list ?? []).some((c) => c.id === maskedComment.id),
    );

    let reviewMissing = false;
    try {
      await request('/admin/comments/__no_such_comment__/review', { method: 'POST', token });
    } catch (e) {
      reviewMissing = e.message === '评论不存在或已删除';
    }
    check('复核未知评论 40405', reviewMissing);

    // 16.10 词库维护：改动作 / 删快照 / 删第二次 40406
    const patched = await request(`/admin/words/${wMask.id}`, {
      method: 'PATCH',
      token,
      body: { action: 'block', category: '契约检查改' },
    });
    check(
      'PATCH 改动作与分组（不许改词本身）',
      patched.action === 'block' && patched.category === '契约检查改' && patched.word === W_MASK,
    );
    const deleted = await request(`/admin/words/${wMask.id}`, { method: 'DELETE', token });
    check('DELETE 返回删除前的快照（审计用）', deleted.word === W_MASK);
    let deleteAgain = false;
    try {
      await request(`/admin/words/${wMask.id}`, { method: 'DELETE', token });
    } catch {
      deleteAgain = true;
    }
    check('删第二次 40406', deleteAgain);

    // 16.11 收尾：删掉临时词与演示评论，避免污染开发库
    for (const id of [wBlock.id, wSub.id, wAllow.id]) {
      try {
        await request(`/admin/words/${id}`, { method: 'DELETE', token });
      } catch {
        /* 已删或不存在：收尾不影响结论 */
      }
    }
    for (const cid of [maskedComment.id, allowedComment.id, subComment.id, afterDisable.id]) {
      try {
        await request(`/news/${DEMO_NEWS}/comments/${cid}`, { method: 'DELETE', token });
      } catch {
        /* 同上 */
      }
    }
    const wordsFinal = await request('/admin/words?limit=500', { token });
    check(
      '收尾后词库回到初始规模',
      wordsFinal.total === beforeAdd,
      `${wordsFinal.total} vs ${beforeAdd}`,
    );
  }

  // 17) 用户昵称敏感词校验（P1-24）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  //     核心口径：注册与改名**同一条链**（形状 42203 / block 40006 / review 放行待复核）；
  //     匹配抗符号隔断与拼音别名（同音字没有字符级算法，靠运营补别名）；allow 白名单
  //     只豁免被完全包裹的片段；命中留痕（含被拦的）；词库 CRUD + 保存即生效；
  //     复核队列幂等且只清标志不改名。
  //     双端通用要点：mock 有 5 条种子词与 2 个待复核账号，**真后端词库零种子**——
  //     故一律用「契约昵称」前缀的自建词（先清遗留再快照总数），队列断言用
  //     「包含演示用户」而非绝对总数；演示昵称收尾恢复原值。
  if (process.env.CHECK_ADMIN === '1' && token) {
    // 重新登录取当前用户形态（第 4 步的 auth 在 if 块内，外层拿不到）
    const demoLogin = await request('/auth/login', { method: 'POST', body: { phone, password } });
    const demoUser = demoLogin.user;
    const originalName = demoUser.name;
    const demoUserId = demoUser.id;

    // 17.1 形状校验（42203）：消息即面向用户的提示，六态逐一比对
    //      （空/纯空白靠 trim；单字与纯符号是两回事；emoji 撞字符集——字符集
    //      先于长度，否则用户会收到"至少 2 个字符"而他再加多少字都过不了）
    const shapeCases = [
      ['', '昵称不能为空'],
      ['   ', '昵称不能为空'],
      ['x', '昵称至少需要 2 个字符'],
      ['昵'.repeat(25), '昵称不能超过 24 个字符'],
      ['昵称😀测试', 'CHARSET'], // 符号表可配置，只断言行式
      ['____', '昵称需至少包含一个字母、数字或汉字'],
    ];
    let shapeOk = 0;
    const shapeBad = [];
    for (const [name, expect] of shapeCases) {
      try {
        await request('/me/nickname', { method: 'PUT', token, body: { name } });
        shapeBad.push(`「${name || '空'}」未被拒`);
      } catch (e) {
        if (expect === 'CHARSET' ? e.message.includes('仅支持') : e.message === expect)
          shapeOk += 1;
        else shapeBad.push(`「${name || '空'}」→ ${e.message}`);
      }
    }
    check(
      '形状校验六态全部 42203 且文案精准（消息即用户提示）',
      shapeOk === shapeCases.length,
      shapeBad.join('；'),
    );

    // 未登录改昵称 → 401（鉴权先于形状，别把未登录报成"昵称不合法"）
    let nick401 = false;
    try {
      await request('/me/nickname', { method: 'PUT', body: { name: '未登录尝试' } });
    } catch (e) {
      nick401 = e.message === '未登录或登录已过期';
    }
    check('未登录改昵称 → 401 且透传提示', nick401);

    // 17.2 词库 CRUD。四个临时词：block / review / 带拼音别名的 review /
    //      allow（整段包裹 review 词——白名单的豁免设计）。刻意互不为子串
    //      （allow 包含 review 是有意的），否则一条用例的文本会命中别的词
    const W_N_BLOCK = '契约昵称拦截';
    const W_N_REVIEW = '契约昵称待核';
    const W_N_ALIAS = '契约昵称主词';
    const W_N_ALLOW = '契约昵称待核整段';
    const ALIAS_PY = 'qiyuebingming'; // 拼音全拼：QiyueBingMing 这类写法靠它拦

    // 先清掉上次异常退出遗留的同前缀词（幂等：本段可反复跑）
    const stale = (
      await request(
        '/admin/nickname-words?keyword=' + encodeURIComponent('契约昵称') + '&limit=500',
        {
          token,
        },
      )
    ).list;
    for (const w of stale ?? []) {
      try {
        await request(`/admin/nickname-words/${w.id}`, { method: 'DELETE', token });
      } catch {
        /* 遗留清理不影响结论 */
      }
    }
    const allNickWords = await request('/admin/nickname-words?limit=500', { token });
    check(
      'GET /admin/nickname-words 返回 {list,total}',
      Array.isArray(allNickWords.list) && typeof allNickWords.total === 'number',
      `${allNickWords.total} 条`,
    );
    check(
      '词行含 word/action/category/aliases/enabled/hitCount 与时间戳',
      (allNickWords.list ?? []).every(
        (w) =>
          typeof w.word === 'string' &&
          ['block', 'review', 'allow'].includes(w.action) &&
          Array.isArray(w.aliases) &&
          typeof w.enabled === 'boolean' &&
          typeof w.hitCount === 'number' &&
          typeof w.updatedAt === 'string',
      ),
    );
    const beforeAdd = allNickWords.total ?? 0;

    const baseCreateAudit =
      (await request(`/admin/audit?target=${encodeURIComponent(W_N_BLOCK)}&limit=100`, { token }))
        .list?.[0]?.id ?? 0;
    const nwBlock = await request('/admin/nickname-words', {
      method: 'POST',
      token,
      body: { word: W_N_BLOCK, action: 'block', category: '契约检查' },
    });
    check(
      'POST /admin/nickname-words 新增成功（block；aliases 恒为数组）',
      nwBlock.word === W_N_BLOCK &&
        nwBlock.action === 'block' &&
        Array.isArray(nwBlock.aliases) &&
        nwBlock.aliases.length === 0 &&
        nwBlock.hitCount === 0,
    );
    check(
      '新增留痕（nicknameWord.create）',
      (
        await request(`/admin/audit?target=${encodeURIComponent(W_N_BLOCK)}&limit=100`, { token })
      ).list.some((a) => a.action === 'nicknameWord.create' && a.id > baseCreateAudit),
    );

    let dupNick = '';
    try {
      await request('/admin/nickname-words', {
        method: 'POST',
        token,
        body: { word: W_N_BLOCK, action: 'review' },
      });
    } catch (e) {
      dupNick = e.message;
    }
    check('重复昵称词被拒（40906）', dupNick === '该昵称敏感词已存在', dupNick);

    let shortNick = '';
    try {
      await request('/admin/nickname-words', { method: 'POST', token, body: { word: '契' } });
    } catch (e) {
      shortNick = e.message;
    }
    check(
      '主词归一化后不足 2 字被拒（42204，文案说明"空格与标点不计"）',
      shortNick === '昵称敏感词至少需 2 个有效字符（空格与标点不计）',
      shortNick,
    );

    let shortAlias = '';
    try {
      await request('/admin/nickname-words', {
        method: 'POST',
        token,
        body: { word: W_N_ALIAS, action: 'review', aliases: ['x'] },
      });
    } catch (e) {
      shortAlias = e.message;
    }
    check(
      '别名逐个查长度：过短别名被拒（42204）且指出是哪条',
      shortAlias === '别名「x」至少需 2 个有效字符（空格与标点不计）',
      shortAlias,
    );
    check(
      '别名过短时整条词都不入库（"配了却没生效"比报错难发现）',
      (
        await request(`/admin/nickname-words?keyword=${encodeURIComponent(W_N_ALIAS)}&limit=500`, {
          token,
        })
      ).list.length === 0,
    );

    const nwReview = await request('/admin/nickname-words', {
      method: 'POST',
      token,
      body: { word: W_N_REVIEW, action: 'review', category: '契约检查' },
    });
    const nwAlias = await request('/admin/nickname-words', {
      method: 'POST',
      token,
      body: { word: W_N_ALIAS, action: 'review', category: '契约检查', aliases: [ALIAS_PY] },
    });
    check(
      'review 词与带别名词均可入库（aliases 原样回显）',
      nwReview.action === 'review' &&
        nwAlias.action === 'review' &&
        nwAlias.aliases.length === 1 &&
        nwAlias.aliases[0] === ALIAS_PY,
    );
    check(
      '新增后总数 +3',
      (await request('/admin/nickname-words?limit=500', { token })).total === beforeAdd + 3,
    );

    // 17.3 命中日志基线：只数**本次运行新增**（持久库跨运行累积，绝对计数必挂）
    const baseNickHitId =
      (await request('/admin/nickname-hits?limit=1', { token })).list?.[0]?.id ?? 0;

    // 17.4 block：命中即拒绝，且**不改昵称**；提示不回显命中词
    let nickBlocked = false;
    let nickBlockMsg = '';
    try {
      await request('/me/nickname', { method: 'PUT', token, body: { name: `${W_N_BLOCK}来了` } });
    } catch (e) {
      nickBlocked = true;
      nickBlockMsg = e.message;
    }
    check('block 词命中 → 改名被拒（40006）', nickBlocked, nickBlockMsg);
    check(
      'block 提示不回显命中词（回显=给绕过者一份对照表）',
      nickBlocked && !nickBlockMsg.includes(W_N_BLOCK),
      nickBlockMsg,
    );
    const afterBlock = await request('/auth/login', { method: 'POST', body: { phone, password } });
    check(
      '被拦后昵称不被改写（拒绝是拒绝，不是"帮你换一个"）',
      afterBlock.user.name === originalName,
      afterBlock.user.name,
    );

    // 17.5 review：放行 + 置待复核（机器的判断是概率性的，先放行再让人看）
    const reviewName = `${W_N_REVIEW}来了`;
    const reviewed = await request('/me/nickname', {
      method: 'PUT',
      token,
      body: { name: reviewName },
    });
    check(
      'review 词命中 → 放行但置待复核',
      reviewed.result === 'review' &&
        reviewed.pendingReview === true &&
        reviewed.name === reviewName,
      `result=${reviewed.result}`,
    );
    const afterReview = await request('/auth/login', { method: 'POST', body: { phone, password } });
    check(
      '登录下发 namePendingReview（刷新不失忆，否则只有管理员看得见）',
      afterReview.user.name === reviewName && afterReview.user.namePendingReview === true,
    );
    const spaced = await request('/me/nickname', {
      method: 'PUT',
      token,
      body: { name: `${W_N_REVIEW.slice(0, 2)}-${W_N_REVIEW.slice(2)}来了` },
    });
    check(
      '符号隔断照样命中（归一化吃掉标点：契约昵-称待核 ≡ 契约昵称待核）',
      spaced.result === 'review' && spaced.pendingReview === true,
      `result=${spaced.result}`,
    );

    // 17.6 别名：拼音全拼是昵称绕过的主力——同音字替换没有字符级算法可解，
    //      由运营补别名（命中日志的 matched 就是"该补哪个别名"的依据）
    const aliasName = 'QiyueBingMing来了';
    const viaAlias = await request('/me/nickname', {
      method: 'PUT',
      token,
      body: { name: aliasName },
    });
    check(
      '别名命中 → 按主词分级（review 放行待复核）',
      viaAlias.result === 'review' && viaAlias.pendingReview === true,
      `result=${viaAlias.result}`,
    );

    // 17.7 命中日志：形状、四维筛选、游标（只读；回答误杀与受理申诉）
    const nickHits = await request('/admin/nickname-hits?limit=20', { token });
    check(
      'GET /admin/nickname-hits 返回 {list,total,hasMore,nextCursor}',
      Array.isArray(nickHits.list) &&
        typeof nickHits.total === 'number' &&
        typeof nickHits.hasMore === 'boolean' &&
        'nextCursor' in nickHits,
      `${nickHits.total} 条`,
    );
    check(
      '命中行含 word/matched/action/source/userId/snippet/createdAt',
      (nickHits.list ?? []).every(
        (h) =>
          typeof h.word === 'string' &&
          typeof h.matched === 'string' &&
          ['block', 'review'].includes(h.action) &&
          ['register', 'update'].includes(h.source) &&
          'userId' in h &&
          typeof h.snippet === 'string' &&
          typeof h.createdAt === 'string',
      ),
    );
    const newHits = (nickHits.list ?? []).filter((h) => h.id > baseNickHitId);
    check(
      '四次提交全部留痕（block 也留：拦了多少次不能是黑箱）',
      newHits.length === 4,
      newHits.map((h) => `${h.word}/${h.matched}/${h.action}`).join(','),
    );
    const aliasHit = newHits.find((h) => h.word === W_N_ALIAS);
    check(
      'matched 记实际命中的模式（回答"是哪条别名拦的"）',
      aliasHit?.matched === ALIAS_PY &&
        aliasHit?.action === 'review' &&
        aliasHit?.source === 'update',
      aliasHit?.matched,
    );
    const blockHit = newHits.find((h) => h.word === W_N_BLOCK);
    check(
      'block 命中 source=update、snippet 是当次提交的昵称原文',
      blockHit?.source === 'update' && blockHit?.snippet === `${W_N_BLOCK}来了`,
      blockHit?.snippet,
    );
    const byWord = await request(
      `/admin/nickname-hits?word=${encodeURIComponent(W_N_BLOCK)}&limit=100`,
      { token },
    );
    check(
      'word 筛选只返回该词命中',
      (byWord.list ?? []).length > 0 && (byWord.list ?? []).every((h) => h.word === W_N_BLOCK),
    );
    const byAction = await request('/admin/nickname-hits?action=review&limit=100', { token });
    check(
      'action=review 只返回待复核命中',
      (byAction.list ?? []).every((h) => h.action === 'review'),
    );
    const bySource = await request('/admin/nickname-hits?source=update&limit=100', { token });
    check(
      'source=update 只返回改名入口',
      (bySource.list ?? []).every((h) => h.source === 'update'),
    );
    const byUser = await request(`/admin/nickname-hits?userId=${demoUserId}&limit=100`, { token });
    check(
      'userId 筛选只看某个用户（队列与日志互相印证）',
      (byUser.list ?? []).length > 0 && (byUser.list ?? []).every((h) => h.userId === demoUserId),
    );
    let badNickAction = false;
    try {
      await request('/admin/nickname-hits?action=bogus', { token });
    } catch {
      badNickAction = true;
    }
    check('非法 action 筛选被拒（422）', badNickAction);

    const nh1 = await request('/admin/nickname-hits?limit=2', { token });
    if (nh1.nextCursor) {
      const nh2 = await request(`/admin/nickname-hits?limit=2&cursor=${nh1.nextCursor}`, { token });
      const ids1 = (nh1.list ?? []).map((h) => h.id);
      check(
        '命中日志游标翻页不重复',
        (nh2.list ?? []).every((h) => !ids1.includes(h.id)),
      );
    }

    // 17.8 复核队列：按人定位；通过只清标志、不改昵称本身
    const queue = await request('/admin/nickname-reviews?limit=100', { token });
    check(
      'GET /admin/nickname-reviews 返回 {list,total}',
      Array.isArray(queue.list) && typeof queue.total === 'number',
      `${queue.total} 条`,
    );
    check(
      '队列行含 userId/name/phone/flaggedAt/flaggedName/flaggedMatched',
      (queue.list ?? []).every(
        (r) =>
          'userId' in r &&
          typeof r.name === 'string' &&
          typeof r.phone === 'string' &&
          'flaggedAt' in r &&
          'flaggedName' in r &&
          'flaggedMatched' in r,
      ),
    );
    const myRow = (queue.list ?? []).find((r) => r.userId === demoUserId);
    check(
      '待复核用户进队（mock 另有 2 个种子账号，故用"包含"而非绝对总数）',
      !!myRow,
      (queue.list ?? []).map((r) => r.userId).join(','),
    );
    check(
      'flaggedName/flaggedMatched 取**最近一条** review 命中（队列回答"哪个提交要人看"）',
      myRow?.flaggedName === aliasName && myRow?.flaggedMatched === ALIAS_PY,
      `${myRow?.flaggedName} / ${myRow?.flaggedMatched}`,
    );
    const flaggedAts = (queue.list ?? []).map((r) => String(r.flaggedAt ?? ''));
    check(
      '队列按 flaggedAt 降序（新提交的浮在上面）',
      flaggedAts.every((v, i) => i === 0 || flaggedAts[i - 1] >= v),
      flaggedAts.join(','),
    );
    const byKw = await request(
      `/admin/nickname-reviews?keyword=${encodeURIComponent(aliasName)}&limit=100`,
      { token },
    );
    check(
      'keyword 按当前昵称筛出该用户',
      (byKw.list ?? []).some((r) => r.userId === demoUserId),
    );

    const baseReviewAudit =
      (await request(`/admin/audit?target=${demoUserId}&limit=100`, { token })).list?.[0]?.id ?? 0;
    const nickReviewed = await request(`/admin/nickname-reviews/${demoUserId}`, {
      method: 'POST',
      token,
    });
    check(
      '复核通过：pendingReview=false 且写 reviewedAt',
      nickReviewed.pendingReview === false && !!nickReviewed.reviewedAt,
      String(nickReviewed.reviewedAt),
    );
    check(
      '复核不改昵称本身（复核是"人看过了"，不是运营替他改名）',
      nickReviewed.name === aliasName,
      nickReviewed.name,
    );
    const afterNickReview = await request('/auth/login', {
      method: 'POST',
      body: { phone, password },
    });
    check(
      '复核后登录下发 namePendingReview=false',
      afterNickReview.user.namePendingReview === false,
    );
    const queueAfter = await request('/admin/nickname-reviews?limit=100', { token });
    check('复核后出队', !(queueAfter.list ?? []).some((r) => r.userId === demoUserId));
    const nickReviewedAgain = await request(`/admin/nickname-reviews/${demoUserId}`, {
      method: 'POST',
      token,
    });
    check('复核幂等（重复调用不报错）', nickReviewedAgain.pendingReview === false);
    const reviewAudits =
      (await request(`/admin/audit?target=${demoUserId}&limit=100`, { token })).list ?? [];
    check(
      '复核留痕（nickname.review，target=用户 id）',
      reviewAudits.some((a) => a.action === 'nickname.review' && a.id > baseReviewAudit),
    );
    check(
      '重复复核不重复审计（没有新事实就不留新痕）',
      reviewAudits.filter((a) => a.id > baseReviewAudit && a.action === 'nickname.review')
        .length === 1,
    );
    let reviewMissingUser = false;
    try {
      await request('/admin/nickname-reviews/999999999', { method: 'POST', token });
    } catch (e) {
      reviewMissingUser = e.message === '用户不存在';
    }
    check('复核未知用户 40408', reviewMissingUser);

    // 17.9 allow 白名单：命中区间被 allow 词**完全包裹**才豁免（降误杀的逃生门）
    const nwAllow = await request('/admin/nickname-words', {
      method: 'POST',
      token,
      body: { word: W_N_ALLOW, action: 'allow', category: '契约检查' },
    });
    check('allow 词可入库', nwAllow.action === 'allow');
    const allowed = await request('/me/nickname', {
      method: 'PUT',
      token,
      body: { name: W_N_ALLOW },
    });
    check(
      'allow 词整段命中 → 豁免其中的 review 词（pass 且清待复核）',
      allowed.result === 'pass' && allowed.pendingReview === false,
      `result=${allowed.result}`,
    );
    const stillReview = await request('/me/nickname', {
      method: 'PUT',
      token,
      body: { name: `${W_N_REVIEW}之外` },
    });
    check(
      '白名单外的同词片段仍命中（白名单只豁免指定片段）',
      stillReview.result === 'review',
      `result=${stillReview.result}`,
    );

    // 17.10 命中计数与排序：hitCount 随留痕累加（高频词要么是热点，要么是误杀大户）
    const wordsAfterHits = (await request('/admin/nickname-words?limit=500', { token })).list;
    const hitCountOf = (w) => wordsAfterHits.find((x) => x.word === w)?.hitCount ?? -1;
    check(
      'hitCount 按留痕累加（block 1 / review 3 / 别名 1）',
      hitCountOf(W_N_BLOCK) === 1 && hitCountOf(W_N_REVIEW) === 3 && hitCountOf(W_N_ALIAS) === 1,
      `${W_N_BLOCK}=${hitCountOf(W_N_BLOCK)} ${W_N_REVIEW}=${hitCountOf(W_N_REVIEW)} ${W_N_ALIAS}=${hitCountOf(W_N_ALIAS)}`,
    );
    check(
      '列表按命中次数降序（运营先看最热的词）',
      wordsAfterHits[0]?.word === W_N_REVIEW,
      wordsAfterHits.map((w) => `${w.word}:${w.hitCount}`).join(','),
    );

    // 17.11 词库维护：改动作（不许改词本身）/ 删快照 / 删第二次 40407 / reload
    const baseUpdateAudit =
      (await request(`/admin/audit?target=${encodeURIComponent(W_N_ALIAS)}&limit=100`, { token }))
        .list?.[0]?.id ?? 0;
    const nwPatched = await request(`/admin/nickname-words/${nwAlias.id}`, {
      method: 'PATCH',
      token,
      body: { action: 'block', category: '契约检查改' },
    });
    check(
      'PATCH 改动作与分组（不许改词本身：改词=删一条加一条）',
      nwPatched.action === 'block' &&
        nwPatched.category === '契约检查改' &&
        nwPatched.word === W_N_ALIAS &&
        nwPatched.aliases[0] === ALIAS_PY,
    );
    check(
      '改动作留痕（nicknameWord.update）',
      (
        await request(`/admin/audit?target=${encodeURIComponent(W_N_ALIAS)}&limit=100`, { token })
      ).list.some((a) => a.action === 'nicknameWord.update' && a.id > baseUpdateAudit),
    );
    // 保存即生效：别名写法从"待复核"变成"拦截"（改词即失效本进程缓存）
    let aliasNowBlocked = false;
    try {
      await request('/me/nickname', { method: 'PUT', token, body: { name: 'QiyueBingMing再来' } });
    } catch {
      aliasNowBlocked = true;
    }
    check('改动作保存即生效（热更新，无需重启）', aliasNowBlocked);

    const baseDeleteAudit =
      (await request(`/admin/audit?target=${encodeURIComponent(W_N_BLOCK)}&limit=100`, { token }))
        .list?.[0]?.id ?? 0;
    const nwDeleted = await request(`/admin/nickname-words/${nwBlock.id}`, {
      method: 'DELETE',
      token,
    });
    check(
      'DELETE 返回删除前的快照（审计用）',
      nwDeleted.word === W_N_BLOCK && nwDeleted.action === 'block',
    );
    check(
      '删除留痕（nicknameWord.delete，含快照）',
      (
        await request(`/admin/audit?target=${encodeURIComponent(W_N_BLOCK)}&limit=100`, { token })
      ).list.some((a) => a.action === 'nicknameWord.delete' && a.id > baseDeleteAudit),
    );
    let deleteAgain = '';
    try {
      await request(`/admin/nickname-words/${nwBlock.id}`, { method: 'DELETE', token });
    } catch (e) {
      deleteAgain = e.message;
    }
    check('删第二次 40407', deleteAgain === '昵称敏感词不存在', deleteAgain);

    const baseReloadAudit = (await request('/admin/audit?limit=100', { token })).list?.[0]?.id ?? 0;
    const reloaded = await request('/admin/nickname-words/reload', { method: 'POST', token });
    check(
      'POST /admin/nickname-words/reload 返回生效条数',
      typeof reloaded.count === 'number' && reloaded.count >= 3,
      String(reloaded.count),
    );
    check(
      'reload 留痕（nicknameWord.reload）',
      (await request('/admin/audit?limit=100', { token })).list.some(
        (a) => a.action === 'nicknameWord.reload' && a.id > baseReloadAudit,
      ),
    );

    // 17.12 收尾：删掉剩余临时词、恢复演示昵称，不污染开发库
    for (const w of [nwBlock, nwReview, nwAlias, nwAllow]) {
      try {
        await request(`/admin/nickname-words/${w.id}`, { method: 'DELETE', token });
      } catch {
        /* 已删或不存在：收尾不影响结论 */
      }
    }
    try {
      // 先删词再恢复：原名若含本轮临时词，删掉后才过得了校验
      await request('/me/nickname', { method: 'PUT', token, body: { name: originalName } });
    } catch {
      /* 原名若已被运营加的新词判违规，保留现状比反复报错好 */
    }
    const restored = await request('/auth/login', { method: 'POST', body: { phone, password } });
    check(
      '收尾后演示昵称与待复核状态回位',
      restored.user.name === originalName &&
        restored.user.namePendingReview === demoUser.namePendingReview,
      `${restored.user.name} / pending=${restored.user.namePendingReview}`,
    );
    const wordsFinal = await request('/admin/nickname-words?limit=500', { token });
    check(
      '收尾后词库回到初始规模',
      wordsFinal.total === beforeAdd,
      `${wordsFinal.total} vs ${beforeAdd}`,
    );
  }

  // 8.99) 负反馈与曝光去重（E3 / P1-72）
  //
  // 四条要钉：曝光批量上报幂等、负反馈二选一为 42207、单条负反馈对所有排序生效、
  // 曝光去重只作用 for-you。收尾取关，不留残留。
  {
    // 未登录 → 401（曝光与负反馈都是账号数据）
    let exp401 = false;
    try {
      await request('/me/exposures', { method: 'POST', body: { ids: ['x'] } });
    } catch (e) {
      exp401 = e.message === '未登录或登录已过期';
    }
    check('无 token 上报曝光 → 401', exp401);

    // 空 ids / 超上限：拒绝而不是静默截断
    //
    // 必须断言**文案**而不只是"抛错了"：`token` 缺失/受限态时这里同样会抛错，
    // 只判抛错会让断言恒真——漏传 DEMO_PASSWORD 时它就是靠这条蒙过去的。
    // 双端文案都含 `ids`（后端「参数校验失败：ids …」/ mock「参数校验失败：ids 不能为空」）。
    let emptyRejected = false;
    let emptyMsg = '';
    try {
      await request('/me/exposures', { method: 'POST', body: { ids: [] }, token });
    } catch (e) {
      emptyMsg = e.message;
      emptyRejected = e.message.includes('ids');
    }
    check('曝光 ids 为空 → 422（校验拒绝，非鉴权失败）', emptyRejected, emptyMsg);
    let overRejected = false;
    let overMsg = '';
    try {
      await request('/me/exposures', {
        method: 'POST',
        body: { ids: Array.from({ length: 51 }, (_, i) => `x${i}`) },
        token,
      });
    } catch (e) {
      overMsg = e.message;
      overRejected = e.message.includes('ids');
    }
    check('曝光 ids 超 50 → 422（不静默截断）', overRejected, overMsg);

    // 负反馈：二选一是域规则 → 42207
    let mutex = false;
    let mutexMsg = '';
    try {
      await request('/me/dislikes', { method: 'POST', body: {}, token });
    } catch (e) {
      mutexMsg = e.message;
      mutex = e.message.includes('newsId') || e.message.includes('只能');
    }
    check('负反馈两个都不给 → 42207', mutex, mutexMsg);

    const feedBefore = await request('/news?sort=for-you&pageSize=3', { token });
    const victim = (feedBefore.list ?? [])[0];
    if (victim) {
      const disliked = await request('/me/dislikes', {
        method: 'POST',
        body: { newsId: victim.id },
        token,
      });
      check('单条负反馈成功并返回集合', (disliked.newsIds ?? []).includes(victim.id));

      // 单条负反馈是显式表达 → latest 也不该再出现它
      const latestIds = (await request('/news?sort=latest&pageSize=50', { token })).list ?? [];
      check(
        '踩过的内容不在 latest 出现（显式表达对所有排序生效）',
        latestIds.every((n) => n.id !== victim.id),
        latestIds.length,
      );

      const removed = await request(`/me/dislikes/news/${victim.id}`, { method: 'DELETE', token });
      check(
        '取消单条负反馈后集合为空（收尾，不留残留）',
        !(removed.newsIds ?? []).includes(victim.id),
      );
    }

    // 曝光：上报后 for-you 不再出现它，而 latest 一字不动
    const expVictim = ((await request('/news?sort=for-you&pageSize=5', { token })).list ?? [])[0];
    if (expVictim) {
      // 基线必须**在上报之前**取：断言的是"latest 不受曝光影响"，
      // 而不是"那条还在 latest 前 50 里"——for-you 按热度排、latest 按时间排，
      // 头条未必落在 latest 的前 50，按条断言会随机失败（本批首跑就红在这里）。
      const latestBefore = ((await request('/news?sort=latest&pageSize=50', { token })).list ?? [])
        .map((n) => n.id)
        .join();
      await request('/me/exposures', { method: 'POST', body: { ids: [expVictim.id] }, token });
      const after = await request('/news?sort=for-you&pageSize=50', { token });
      const ids = (after.list ?? []).map((n) => n.id);
      check(
        '上报曝光后 for-you 不再出现它（或探底保护放行）',
        !ids.includes(expVictim.id) || after.dedupRelaxed === true,
        `dedupRelaxed=${after.dedupRelaxed}`,
      );
      const latestAfter = ((await request('/news?sort=latest&pageSize=50', { token })).list ?? [])
        .map((n) => n.id)
        .join();
      check(
        '曝光不改变 latest（时间流完整）',
        latestAfter === latestBefore && latestBefore.length > 0,
        `${latestBefore.split(',').length} → ${latestAfter.split(',').length}`,
      );
    }
  }

  // 15) 枚举逐值校验（P1-85）：前端常量与后端白名单**逐字**比对
  //
  // 之前这条是"文档承诺了但没实现"：`src/constants/reports.spec.ts` 写着
  // "逐字比对放在契约脚本（它会拿真实值打接口）"，而本脚本只硬编码打了
  // 6 个理由里的 2 个、7 个频道里的 3 个——后端改一个枚举值，
  // 前端测试照样绿（它硬编码的是旧值），只有点到那个选项的用户会撞 422。
  //
  // 做法：本脚本**读前端常量文件**取值（不在脚本里抄一份，避免第四处来源），
  // 再拿每个真实值打一次接口。漂移于是在 CI 阶段就暴露。
  {
    const channels = readConstValues(channelsFile, 'CHANNELS');
    const reasons = readConstValues(reasonsFile, 'REPORT_REASONS');
    const commentSorts = readConstValues(commentSortsFile, 'COMMENT_SORTS');

    check(
      '能从前端常量文件读出枚举（读不到即脚本失效，不能算"通过"）',
      channels.length >= 7 && reasons.length >= 6 && commentSorts.length >= 2,
      `频道 ${channels.length} / 理由 ${reasons.length} / 评论排序 ${commentSorts.length}`,
    );

    // 频道：每个 key 都要被后端接受（改名/删除会在这里红）
    const badChannels = [];
    for (const key of channels) {
      try {
        await request(`/news?channel=${encodeURIComponent(key)}&pageSize=1`);
      } catch {
        badChannels.push(key);
      }
    }
    check(
      `全部 ${channels.length} 个频道都被后端接受`,
      badChannels.length === 0,
      badChannels.length ? `被拒：${badChannels.join(',')}` : channels.join(','),
    );

    // 举报理由：逐值提交（同一用户同一目标幂等，不新增行，只更新理由）
    if (token) {
      const badReasons = [];
      for (const reason of reasons) {
        try {
          await request('/reports', {
            method: 'POST',
            token,
            body: { targetType: 'news', targetId: 'n4', reason },
          });
        } catch {
          badReasons.push(reason);
        }
      }
      check(
        `全部 ${reasons.length} 个举报理由都被后端接受（白名单逐字一致）`,
        badReasons.length === 0,
        badReasons.length ? `被拒：${badReasons.join(',')}` : reasons.join(','),
      );
    }

    // 评论排序：逐值请求
    const badSorts = [];
    for (const sort of commentSorts) {
      try {
        await request(`/news/n4/comments?sort=${encodeURIComponent(sort)}`, { token });
      } catch {
        badSorts.push(sort);
      }
    }
    check(
      `全部 ${commentSorts.length} 种评论排序都被后端接受`,
      badSorts.length === 0,
      badSorts.length ? `被拒：${badSorts.join(',')}` : commentSorts.join(','),
    );
  }

  console.log(`\n=== 结果：${pass} 通过 / ${fail} 失败 ===\n`);
  process.exit(fail === 0 ? 0 : 1);
};

run().catch((e) => {
  console.error('\n[契约检查异常]', e.message);
  process.exit(1);
});
