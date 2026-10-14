/**
 * 前后端契约兼容性验证脚本（Node，零依赖）
 *
 * 复刻前端 `src/api/http.ts` 的解包/鉴权逻辑与 `src/api/news.ts` 的字段归一化，
 * 对 FastAPI 后端（或本地 mock 后端）跑一遍，确认契约完全兼容（字段名、错误码、鉴权头）。
 *
 * 覆盖：资讯列表/详情/排序、搜索与热搜、登录、评论（含二级评论：回复结构 /
 * 正序分页 / 预览 / 删除语境）、点赞、收藏历史、RSS 源管理、管理员白名单、
 * 源健康历史与趋势（P1-19）、资讯审核与下架（P1-20）。
 *
 * 用法：先启动后端，再执行 node scripts/contract-check.mjs
 */
const BASE = process.env.API_BASE || 'http://127.0.0.1:8000/api';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---- 复刻 api/http.ts 的 request() ---- */
async function request(path, { method = 'GET', body, token } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
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

  // 2) 资讯详情
  const detail = normalize(await request(`/news/${items[0].id}`));
  check('GET /news/:id 详情', detail.id === items[0].id);

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

    const removedSrc = await request(`/admin/sources/${tmpKey}`, { method: 'DELETE', token });
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
      await request(`/admin/users/${tmpAdmin}`, { method: 'DELETE', token });
    } catch {
      /* ignore */
    }

    const added = await request('/admin/users', {
      method: 'POST',
      token,
      body: { phone: tmpAdmin, note: '契约临时管理员' },
    });
    check(
      'POST /admin/users 添加成功',
      added.phone === tmpAdmin && added.note === '契约临时管理员',
    );
    check('addedBy 记录操作者（审计用）', added.addedBy === phone, String(added.addedBy));

    let dupBlocked = false;
    try {
      await request('/admin/users', { method: 'POST', token, body: { phone: tmpAdmin } });
    } catch {
      dupBlocked = true;
    }
    check('重复添加被拒（409）', dupBlocked);

    // 当前账号自身就在白名单里（CHECK_ADMIN 的前提），再添加自己应被拒
    let selfBlocked = false;
    try {
      await request('/admin/users', { method: 'POST', token, body: { phone } });
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

    const removed = await request(`/admin/users/${tmpAdmin}`, { method: 'DELETE', token });
    check('DELETE /admin/users/:phone 移除成功', removed.phone === tmpAdmin);

    let removeMissing = false;
    try {
      await request(`/admin/users/${tmpAdmin}`, { method: 'DELETE', token });
    } catch {
      removeMissing = true;
    }
    check('重复移除被拒（404）', removeMissing);
  }

  // 12) 管理端：运营审计（P1-15）
  //     默认跳过：要求当前账号具备管理能力（CHECK_ADMIN=1）。
  if (process.env.CHECK_ADMIN === '1' && token) {
    const auditKey = '__contract_audit';
    try {
      await request(`/admin/sources/${auditKey}`, { method: 'DELETE', token });
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
    await request(`/admin/sources/${auditKey}`, { method: 'DELETE', token });
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

  console.log(`\n=== 结果：${pass} 通过 / ${fail} 失败 ===\n`);
  process.exit(fail === 0 ? 0 : 1);
};

run().catch((e) => {
  console.error('\n[契约检查异常]', e.message);
  process.exit(1);
});
