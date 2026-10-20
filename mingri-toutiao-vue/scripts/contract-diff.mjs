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
 * 用法：两端都起着，再执行
 *   API_REAL=http://127.0.0.1:8000/api API_MOCK=http://127.0.0.1:3001/api \
 *     node scripts/contract-diff.mjs
 * 任一端不可达时**跳过并退出 0**（不阻塞本地主流程），
 * CI 里用 DIFF_REQUIRE_BOTH=1 把它变成硬失败。
 */
const REAL = process.env.API_REAL || 'http://127.0.0.1:8000/api';
const MOCK = process.env.API_MOCK || 'http://127.0.0.1:3001/api';
const REQUIRE_BOTH = process.env.DIFF_REQUIRE_BOTH === '1';

/**
 * 已声明且有意保留的差异：命中即跳过（打印为 KNOWN），新增漂移仍会失败。
 * 加一条必须写清理由——否则这里会慢慢变成"什么都能塞"的垃圾桶。
 */
const KNOWN_DIVERGENCES = [
  {
    path: '/news',
    field: 'sourceKey',
    reason:
      'mock 用 mockSourceKey(名称) 合成 key，让离线演示能展示「关注」；' +
      '真后端种子资讯的来源（新华社等）不在 RSS 源表里，source_key 为 null',
  },
];

/** 未登录可打的用例：公开读 + 已知错误路径（错误路径最见真章） */
const CASES = [
  ['GET /news 列表', '/news?channel=tech'],
  ['GET /news?sort=hot 排序', '/news?channel=tech&sort=hot'],
  ['GET /news 详情', '/news/n1'],
  ['GET /news/search 搜索', '/news/search?keyword=%E9%A3%9E%E6%9C%BA'],
  ['GET /search/suggest 联想', '/search/suggest?q=%E9%A3%9E'],
  ['GET /news/:id 不存在', '/news/__no_such__'],
  ['GET /news 非法排序', '/news?sort=__bad__'],
  ['GET /me/favorites 未登录', '/me/favorites'],
  ['GET /news/n1/comments 评论列表', '/news/n1/comments'],
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

/** 递归取形状：对象→字段名+类型；数组→首个元素形状；标量→类型 */
function shapeOf(v, depth = 0) {
  if (depth > 6) return '"…"';
  if (Array.isArray(v)) return ['[]', v.length ? shapeOf(v[0], depth + 1) : null];
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v).sort()) out[k] = shapeOf(v[k], depth + 1);
    return out;
  }
  return v === null ? 'null' : typeof v;
}

async function hit(base, path) {
  const res = await fetch(`${base}${path}`, { headers: { 'Content-Type': 'application/json' } });
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    /* 非 JSON：两边都非 JSON 也算一致 */
  }
  return { status: res.status, json };
}

/** 形状不一致时给出「具体到字段」的差异行 */
function diff(a, b, at = '$') {
  const out = [];
  if (Array.isArray(a) || Array.isArray(b)) {
    // 比元素形状，不比条数（条数是数据）
    if (!Array.isArray(a) || !Array.isArray(b)) {
      out.push(`${at}: 一边是数组一边不是`);
      return out;
    }
    const ae = a[1];
    const be = b[1];
    // 空数组没有元素形状可比：只有一边为空时才提示（可能是筛选条件不同）
    if (ae === null || be === null) {
      if (ae !== be) out.push(`${at}: 一边为空数组`);
      return out;
    }
    return diff(ae, be, `${at}[]`);
  }
  if (a && typeof a === 'object' && b && typeof b === 'object') {
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      // a = mock 形状，b = 真后端形状（调用处 diff(mock, real)）
      if (!(k in a)) out.push(`${at}.${k} 仅真后端有（mock 缺）`);
      else if (!(k in b)) out.push(`${at}.${k} 仅 mock 有（真后端缺）`);
      else out.push(...diff(a[k], b[k], `${at}.${k}`));
    }
    return out;
  }
  if (a !== b) out.push(`${at}: ${JSON.stringify(a)} ≠ ${JSON.stringify(b)}`);
  return out;
}

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
  let pass = 0;
  let fail = 0;

  for (const [name, path] of CASES) {
    const [r, m] = await Promise.all([hit(REAL, path), hit(MOCK, path)]);
    const rs = shapeOf(r.json);
    const ms = shapeOf(m.json);
    const problems = [];
    if (r.status !== m.status) problems.push(`HTTP ${r.status} ≠ ${m.status}`);
    problems.push(...diff(ms, rs));

    const known = problems.filter((p) =>
      KNOWN_DIVERGENCES.some((k) => path.startsWith(k.path) && p.includes(k.field)),
    );
    const real = problems.filter((p) => !known.includes(p));

    if (real.length) {
      fail++;
      console.log(`  FAIL  ${name} (${path})`);
      for (const p of real.slice(0, 8)) console.log(`        ${p}`);
      if (real.length > 8) console.log(`        …另 ${real.length - 8} 处`);
    } else {
      pass++;
      const tail = known.length ? ` — 已声明差异 ${known.length} 处` : '';
      const code =
        r.json && typeof r.json === 'object' && 'code' in r.json ? ` (code=${r.json.code})` : '';
      console.log(`  PASS  ${name}${code}${tail}`);
    }
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
};

run();
