/**
 * 差分契约测试：同一份请求清单分别打 mock 与真后端，比对**响应形状**。
 *
 * 与 `contract-check.mjs` 的分工：那个验「前端期望 vs 某一端」，这个验
 * 「两端彼此一致」。契约漂移的表现是「mock 演示正常、接真后端对不上」，
 * 此前没有任何自动化能发现它。
 *
 * 比的是形状而非取值（id/时间/条数必然不同）：字段集合、字段类型、
 * 嵌套结构、HTTP 状态码、业务 code。
 *
 * P1-86（D8）：扩展到**需登录**端点。此前只覆盖 9 个免鉴权用例，
 * 30 个 `/api/admin/*` 与 `/api/me/*` 从未互相比对过——而首次运行差分（D4-a）
 * 就抓到 3 处真实漂移，未覆盖区域大概率还有。
 *
 * 三条边界（都是刻意的）：
 * 1. **只比 GET**：写操作会在真后端（开发库）里造数据，且不幂等，
 *    跑一次脏一次。写路径的一致性由 `contract-check.mjs` 的行为断言负责。
 * 2. **只比两端都实现的端点**：`/api/messages` 不走 mock server
 *    （前端 `api/messages.ts` 自带 mock 短路），没有可比对象。
 * 3. **只比不需要动态 id 的端点**：逐个接口取 id 再拼路径会让脚本变成
 *    一个小型测试框架，与"形状比对"这个单一职责不符。
 *
 * 用法：两端都起着，再执行
 *   API_REAL=http://127.0.0.1:8000/api API_MOCK=http://127.0.0.1:3001/api \
 *     DEMO_PASSWORD=… node scripts/contract-diff.mjs
 * 任一端不可达时**跳过并退出 0**（不阻塞本地主流程），
 * CI 里用 DIFF_REQUIRE_BOTH=1 把它变成硬失败。
 * 没给 DEMO_PASSWORD 时，需登录用例整体跳过（不伪装成通过）。
 */
const REAL = process.env.API_REAL || 'http://127.0.0.1:8000/api';
const MOCK = process.env.API_MOCK || 'http://127.0.0.1:3001/api';
const REQUIRE_BOTH = process.env.DIFF_REQUIRE_BOTH === '1';
const DEMO_PHONE = process.env.DEMO_PHONE || '13800138000';
const DEMO_PASSWORD = process.env.DEMO_PASSWORD || '';
/** 写路径回放总开关（P1-88）：默认关。会自动写库的脚本不该是默认行为 */
const ALLOW_WRITES = process.env.DIFF_ALLOW_WRITES === '1';

/**
 * 已声明且有意保留的差异：命中即跳过（打印为 KNOWN），新增漂移仍会失败。
 * 加一条必须写清理由——否则这里会慢慢变成"什么都能塞"的垃圾桶。
 */
/**
 * 已声明且有意保留的差异：命中即跳过（打印为 KNOWN），新增漂移仍会失败。
 * 加一条必须写清理由——否则这里会慢慢变成"什么都能塞"的垃圾桶。
 *
 * `match` 必须是**正则且精确到具体差异**，不能只写字段名：第一版用
 * `p.includes(field)`（字段名包含），结果整条 `ctx` 子树全被压制——
 * 反向变异往里塞一个多余键，差分照旧全绿（护栏空转）。宁可多写几个字，
 * 也要让"以后新增的 ctx 漂移"落在失败而不是压制里。
 */
const KNOWN_DIVERGENCES = [
  {
    path: '/news',
    match: /sourceKey/,
    reason:
      'mock 用 mockSourceKey(名称) 合成 key，让离线演示能展示「关注」；' +
      '真后端种子资讯的来源（新华社等）不在 RSS 源表里，source_key 为 null',
  },
  {
    path: '/admin/news',
    // 精确匹配「mock 缺 ctx 的某个子键」这一种。第一版写成
    // /errors\[\]\.ctx 仅真后端有/，而实际差异串是
    // `$.data.errors[].ctx.pattern 仅真后端有`（mock 的 ctx 是空对象、不是没有 ctx）
    // —— 正则没匹配上，已声明差异被当成新漂移报了出来。
    match: /errors\[\]\.ctx\.\w+ 仅真后端有/,
    reason:
      'P1-86：422 信封的 errors[].ctx 是真后端 Pydantic 的实现细节（pattern/min_length…），' +
      'mock 整体不带（文案是人话、type 固定 value_error）。**前端目前没有任何消费方读 ' +
      'data.errors**（全仓 grep 为空），所以这是"形状不完全一致"而非功能缺陷；' +
      'errors 数组本身与 loc/msg/type 的字段集合两端一致。' +
      '**只压制「mock 缺 ctx」这一种**：ctx 里多出/少了别的键仍算失败——' +
      '若将来前端要按 ctx 做字段级提示，这条差异必须整体消掉',
  },
];

/**
 * 用例清单：`[名称, 路径, 模式]`，模式缺省为 `anon`。
 *
 * - `anon`：未登录（公开读 + 已知错误路径——错误路径最见真章）
 * - `auth`：需登录（`/api/me/*`）
 * - `admin`：需管理员（`/api/admin/*`，后端 router 级 `require_admin`）
 */
const CASES = [
  // ---- 免鉴权（D4-a 原有）----
  ['GET /news 列表', '/news?channel=tech'],
  ['GET /news?sort=hot 排序', '/news?channel=tech&sort=hot'],
  ['GET /news 详情', '/news/n1'],
  ['GET /news/search 搜索', '/news/search?keyword=%E9%A3%9E%E6%9C%BA'],
  ['GET /search/suggest 联想', '/search/suggest?q=%E9%A3%9E'],
  ['GET /news/:id 不存在', '/news/__no_such__'],
  ['GET /news 非法排序', '/news?sort=__bad__'],
  ['GET /me/favorites 未登录', '/me/favorites'],
  ['GET /news/n1/comments 评论列表', '/news/n1/comments'],

  // ---- P1-86（D8）：需登录 ----
  ['GET /me/favorites 已登录', '/me/favorites', 'auth'],
  ['GET /me/history 已登录', '/me/history', 'auth'],
  ['GET /me/follows 已登录', '/me/follows', 'auth'],
  ['GET /me/dislikes 已登录', '/me/dislikes', 'auth'],

  // ---- P1-86（D8）：需管理员（此前 0 覆盖）----
  ['GET /admin/dashboard 运营大盘', '/admin/dashboard', 'admin'],
  ['GET /admin/sources 源列表', '/admin/sources', 'admin'],
  ['GET /admin/sources/:key/health 源健康趋势', '/admin/sources/aihot_picks/health', 'admin'],
  ['GET /admin/news 资讯审核列表', '/admin/news', 'admin'],
  ['GET /admin/comments 评论治理列表', '/admin/comments', 'admin'],
  ['GET /admin/words 敏感词库', '/admin/words', 'admin'],
  ['GET /admin/moderation/hits 敏感词命中', '/admin/moderation/hits', 'admin'],
  ['GET /admin/nickname-words 昵称词库', '/admin/nickname-words', 'admin'],
  ['GET /admin/nickname-hits 昵称命中', '/admin/nickname-hits', 'admin'],
  ['GET /admin/nickname-reviews 昵称复核队列', '/admin/nickname-reviews', 'admin'],
  ['GET /admin/reports 举报复核队列', '/admin/reports', 'admin'],
  ['GET /admin/users 管理员白名单', '/admin/users', 'admin'],
  ['GET /admin/audit 运营审计', '/admin/audit', 'admin'],
  ['GET /admin/news 非法筛选', '/admin/news?status=__bad__', 'admin'],
];

async function ping(base) {
  try {
    const res = await fetch(`${base}/news?channel=tech`, { method: 'GET' });
    await res.text();
    return true;
  } catch {
    return false;
  }
}

/** `shapeOf` 对 null 的形状哨兵（字符串，不是 JS null） */
const NULL_SHAPE = 'null';

/** 递归取形状：对象→字段名+类型；数组→首个元素形状；标量→类型 */
function shapeOf(v, depth = 0) {
  if (depth > 6) return '"…"';
  if (Array.isArray(v)) return ['[]', v.length ? shapeOf(v[0], depth + 1) : null];
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v).sort()) out[k] = shapeOf(v[k], depth + 1);
    return out;
  }
  return v === null ? NULL_SHAPE : typeof v;
}

/**
 * 登录取 token（P1-86）。两端用**同一个演示账号**——比的是契约形状，
 * 不是数据内容；账号不同的话，条数与 id 的差异会淹没形状差异。
 */
async function login(base) {
  if (!DEMO_PASSWORD) return null;
  try {
    const res = await fetch(`${base}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: DEMO_PHONE, password: DEMO_PASSWORD }),
    });
    const json = await res.json();
    const token = json?.data?.token;
    if (!token) return null;
    return { token, isAdmin: json?.data?.user?.isAdmin === true };
  } catch {
    return null;
  }
}

async function hit(base, path, token, method = 'GET', body) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${base}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    /* 非 JSON：两边都非 JSON 也算一致 */
  }
  return { status: res.status, json };
}

/**
 * 形状不一致时给出「具体到字段」的差异行。
 *
 * 返回 `{ problems, inconclusive }`——**两者必须分开**（P1-86 的关键修正）：
 * 两端数据集不同（真后端是开发库、mock 是固定 fixture），于是
 * "一边有数据一边没有"是常态。若把这些和真漂移混在一起报，14 条假警报
 * 会淹没 1 条真问题，工具很快就被人忽略。
 *
 * 判定为 **inconclusive（不比较，也不算通过）** 的两种情形：
 * 1. 一边是空数组：没有元素形状可比，无从判断字段是否一致；
 * 2. `$.data` **以内**的字段一边为 null：可空字段恰好没数据，
 *    它说明不了类型是否一致（真正的类型漂移要等两边都有值时才看得见）。
 *
 * **例外：`$.data` 本身不许 null 一边、非空一边**——那是响应信封的形状差异，
 * 不是数据有无（真后端 422 会在 data 里带 `errors`，mock 给 null 就是真漂移）。
 */
function diff(a, b, at = '$') {
  const problems = [];
  const inconclusive = [];

  // 情形 2：null 容错（但 $.data 信封本身不容错）。
  // 注意 `shapeOf` 对 null 返回的是**字符串哨兵** `'null'`，不是 JS 的 null——
  // 这里要跟哨兵比，写成 `a === null` 会永远不成立（第一版就踩了这个空转）。
  const isNullShape = (v) => v === NULL_SHAPE;
  if ((isNullShape(a) || isNullShape(b)) && at !== '$.data') {
    inconclusive.push(`${at}: 一边为 null（可空字段的数据有无，非契约差异）`);
    return { problems, inconclusive };
  }

  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) {
      problems.push(`${at}: 一边是数组一边不是`);
      return { problems, inconclusive };
    }
    const ae = a[1];
    const be = b[1];
    // 情形 1：空数组没有元素形状可比
    if (ae === null || be === null) {
      inconclusive.push(`${at}: 一边为空数组（无法比对元素形状）`);
      return { problems, inconclusive };
    }
    return diff(ae, be, `${at}[]`);
  }

  if (a && typeof a === 'object' && b && typeof b === 'object') {
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      // a = mock 形状，b = 真后端形状（调用处 diff(mock, real)）
      if (!(k in a)) problems.push(`${at}.${k} 仅真后端有（mock 缺）`);
      else if (!(k in b)) problems.push(`${at}.${k} 仅 mock 有（真后端缺）`);
      else {
        const sub = diff(a[k], b[k], `${at}.${k}`);
        problems.push(...sub.problems);
        inconclusive.push(...sub.inconclusive);
      }
    }
    return { problems, inconclusive };
  }

  if (a !== b) problems.push(`${at}: ${JSON.stringify(a)} ≠ ${JSON.stringify(b)}`);
  return { problems, inconclusive };
}

/**
 * 写路径回调用例（P1-88）。**默认不跑**，要显式 `DIFF_ALLOW_WRITES=1`——
 * 一个会自动写库的脚本绝不该是默认行为（打错 `API_REAL` 就是在往生产灌数据）。
 *
 * 每条都是「写入 → 比形状 → 撤销」：
 * - `undo(created)` 收**该侧自己的创建响应**：两端生成的 id 不同
 *   （mock 是 `c_<时间戳>`、真后端是自己的规则），撤销必须各用各的；
 * - 撤销放在 `finally` 里：比对失败也要收摊，不能把测试数据留在库里；
 * - `undo: null` 表示天然幂等、无需撤销（举报按「举报人 × 目标」唯一）。
 *
 * 刻意不收的几类（理由写全，免得后人以为是遗漏）：
 * - `POST /me/follows`：**没有两端都合法的 sourceKey**（mock 的 key 由来源名称
 *   哈希派生，真后端是 RSS 源 key），打过去必然一边 422——那是数据不对等，
 *   不是契约漂移，会制造假警报；
 * - `POST /news/{id}/likes`：mock 未实现，没有可比对象；
 * - `PUT /me/favorites`（合并）/ `PUT /me/history`：要构造合并语义，
 *   收益不抵复杂度，留给契约检查的行为断言；
 * - `/api/admin/*` 写端点：改的是运营配置与审核状态，undo 语义各异
 *   （hide/restore 成对，但 sources/words 的增删会在开发库留痕）。
 */
const WRITE_CASES = [
  {
    name: 'POST /me/favorites 收藏',
    method: 'POST',
    path: '/me/favorites',
    body: { newsId: 'n1' },
    undo: () => ({ method: 'DELETE', path: '/me/favorites/n1' }),
  },
  {
    name: 'POST /me/dislikes 负反馈',
    method: 'POST',
    path: '/me/dislikes',
    body: { newsId: 'n1' },
    undo: () => ({ method: 'DELETE', path: '/me/dislikes/news/n1' }),
  },
  {
    name: 'POST /news/:id/comments 发评论',
    method: 'POST',
    path: '/news/n1/comments',
    body: { content: `差分契约写路径探针 ${new Date().toISOString()}` },
    undo: (created) => ({
      method: 'DELETE',
      path: `/news/n1/comments/${created?.data?.id ?? '__none__'}`,
    }),
  },
  {
    // 幂等：同一用户对同一目标重复举报只更新理由，不新增行
    name: 'POST /reports 举报',
    method: 'POST',
    path: '/reports',
    body: { targetType: 'news', targetId: 'n4', reason: '广告营销' },
    undo: null,
  },
];

const run = async () => {
  const [realOk, mockOk] = await Promise.all([ping(REAL), ping(MOCK)]);
  if (!realOk || !mockOk) {
    const missing = [!realOk && REAL, !mockOk && MOCK].filter(Boolean).join(' 与 ');
    if (REQUIRE_BOTH) {
      console.error(`差分契约测试要求两端都在线，未连上：${missing}`);
      process.exit(1);
    }
    console.log(`跳过差分契约测试（未连上：${missing}）。两端都起着才会比对。`);
    process.exit(0);
  }

  console.log(`\n=== 差分契约测试：mock ${MOCK} ↔ 真后端 ${REAL} ===\n`);

  // 需登录用例的前提：两端都登得上，且 `admin` 用例要求两端都判定为管理员。
  // 拿不到凭据就**明确跳过**，不能让用例伪装成通过。
  const [realAuth, mockAuth] = await Promise.all([login(REAL), login(MOCK)]);
  const authReady = Boolean(realAuth && mockAuth);
  const adminReady = authReady && realAuth.isAdmin && mockAuth.isAdmin;
  if (DEMO_PASSWORD) {
    console.log(
      `  凭据：登录 ${authReady ? '成功' : '失败'}；管理员 ${adminReady ? '是' : '否'}` +
        `（需登录 ${CASES.filter((c) => c[2] === 'auth').length} 条 /` +
        ` 需管理员 ${CASES.filter((c) => (c[2] ?? 'anon') === 'admin').length} 条）\n`,
    );
  } else {
    console.log(
      `  未给 DEMO_PASSWORD：需登录/需管理员用例共 ` +
        `${CASES.filter((c) => c[2] && c[2] !== 'anon').length} 条将跳过\n`,
    );
  }

  let pass = 0;
  let fail = 0;
  let skipped = 0;

  for (const [name, path, mode = 'anon'] of CASES) {
    const token = mode === 'anon' ? undefined : realAuth?.token;
    const mockToken = mode === 'anon' ? undefined : mockAuth?.token;

    if (mode === 'auth' && !authReady) {
      skipped++;
      console.log(`  SKIP  ${name} (${path}) — 两端未能登录`);
      continue;
    }
    if (mode === 'admin' && !adminReady) {
      skipped++;
      console.log(`  SKIP  ${name} (${path}) — 该账号在两端不都是管理员`);
      continue;
    }

    const [r, m] = await Promise.all([hit(REAL, path, token), hit(MOCK, path, mockToken)]);
    const rs = shapeOf(r.json);
    const ms = shapeOf(m.json);
    const problems = [];
    if (r.status !== m.status) problems.push(`HTTP ${r.status} ≠ ${m.status}`);
    const { problems: shapeProblems, inconclusive } = diff(ms, rs);
    problems.push(...shapeProblems);

    const known = problems.filter((p) =>
      KNOWN_DIVERGENCES.some((k) => path.startsWith(k.path) && k.match.test(p)),
    );
    const real = problems.filter((p) => !known.includes(p));

    if (real.length) {
      fail++;
      console.log(`  FAIL  ${name} (${path})`);
      for (const p of real.slice(0, 8)) console.log(`        ${p}`);
      if (real.length > 8) console.log(`        …另 ${real.length - 8} 处`);
    } else {
      pass++;
      const tail =
        known.length || inconclusive.length
          ? ` — 已声明 ${known.length} 处 / 无可比数据 ${inconclusive.length} 处`
          : '';
      const code =
        r.json && typeof r.json === 'object' && 'code' in r.json ? ` (code=${r.json.code})` : '';
      console.log(`  PASS  ${name}${code}${tail}`);
    }
    // 无可比数据的地方**显式列出**：这是工具的盲区，不报出来会让人以为比过了
    for (const note of inconclusive.slice(0, 3)) console.log(`        (盲区) ${note}`);
    if (inconclusive.length > 3) console.log(`        (盲区) …另 ${inconclusive.length - 3} 处`);
  }

  // 16) 写路径回放（P1-88）：默认跳过，DIFF_ALLOW_WRITES=1 才跑
  if (!ALLOW_WRITES) {
    console.log(
      `\n写路径回放未启用（${WRITE_CASES.length} 条）。它会往两端写数据并撤销，` +
        `确认 API_REAL 不是生产库后加 DIFF_ALLOW_WRITES=1 再跑。`,
    );
  } else if (!authReady) {
    console.log('\n写路径回放跳过：两端未能登录。');
  } else {
    console.log(`\n--- 写路径回放（${WRITE_CASES.length} 条，跑完即撤销）---`);
    for (const c of WRITE_CASES) {
      let realCreated = null;
      let mockCreated = null;
      try {
        const [r, m] = await Promise.all([
          hit(REAL, c.path, realAuth.token, c.method, c.body),
          hit(MOCK, c.path, mockAuth.token, c.method, c.body),
        ]);
        realCreated = r.json;
        mockCreated = m.json;

        const problems = [];
        if (r.status !== m.status) problems.push(`HTTP ${r.status} ≠ ${m.status}`);
        const { problems: shapeProblems, inconclusive } = diff(
          shapeOf(mockCreated),
          shapeOf(realCreated),
        );
        problems.push(...shapeProblems);

        if (problems.length) {
          fail++;
          console.log(`  FAIL  ${c.name}`);
          for (const p of problems.slice(0, 8)) console.log(`        ${p}`);
        } else {
          pass++;
          const tail = inconclusive.length ? ` — 无可比数据 ${inconclusive.length} 处` : '';
          console.log(`  PASS  ${c.name}${tail}`);
        }
      } finally {
        // 撤销必须各用各的创建响应；finally 保证比对失败也收摊
        for (const [base, token, created] of [
          [REAL, realAuth.token, realCreated],
          [MOCK, mockAuth.token, mockCreated],
        ]) {
          if (!c.undo || !created) continue;
          const u = c.undo(created);
          try {
            await hit(base, u.path, token, u.method);
          } catch {
            console.log(`        (警告) 撤销失败：${u.method} ${u.path}`);
          }
        }
      }
    }
  }

  console.log(`\n${pass} passed, ${fail} failed, ${skipped} skipped`);
  process.exit(fail ? 1 : 0);
};

run();
