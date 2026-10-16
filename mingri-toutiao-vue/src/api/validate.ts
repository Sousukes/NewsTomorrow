/**
 * 轻量运行时校验（**零依赖**）。
 *
 * 目的：后端字段命名 / 类型发生漂移时能尽早暴露，而不是把脏数据静默写进 store
 * （此前全靠 `as` 断言，编译期无保护、运行期无提示）。
 *
 * 设计：校验器只做"必需字段存在 + 可选字段类型正确"，返回问题清单而非抛异常，
 * 因此不会因为个别脏数据打断整页渲染。若后续引入 zod，可整体替换本文件。
 */

export interface ValidationIssue {
  path: string;
  message: string;
}

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function requireString(o: Obj, key: string, out: ValidationIssue[]): void {
  const v = o[key];
  if (typeof v !== 'string' || v === '') out.push({ path: key, message: '缺少非空字符串' });
}

function optionalString(o: Obj, key: string, out: ValidationIssue[]): void {
  const v = o[key];
  if (v !== undefined && v !== null && typeof v !== 'string') {
    out.push({ path: key, message: '应为字符串' });
  }
}

function optionalNumber(o: Obj, key: string, out: ValidationIssue[]): void {
  const v = o[key];
  if (v !== undefined && v !== null && typeof v !== 'number') {
    out.push({ path: key, message: '应为数字' });
  }
}

function optionalBoolean(o: Obj, key: string, out: ValidationIssue[]): void {
  const v = o[key];
  if (v !== undefined && v !== null && typeof v !== 'boolean') {
    out.push({ path: key, message: '应为布尔' });
  }
}

function optionalStringArray(o: Obj, key: string, out: ValidationIssue[]): void {
  const v = o[key];
  if (v === undefined || v === null) return;
  if (!Array.isArray(v) || v.some((x) => typeof x !== 'string')) {
    out.push({ path: key, message: '应为字符串数组' });
  }
}

/** 资讯项 */
export function validateNewsItem(raw: unknown): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  if (!isObj(raw)) return [{ path: '', message: '不是对象' }];
  requireString(raw, 'id', out);
  requireString(raw, 'title', out);
  optionalString(raw, 'summary', out);
  // E1：正文（详情接口下发，按段落 \n 分隔）
  optionalString(raw, 'content', out);
  optionalString(raw, 'source', out);
  optionalString(raw, 'time', out);
  optionalString(raw, 'cover', out);
  optionalString(raw, 'videoUrl', out);
  optionalNumber(raw, 'commentCount', out);
  optionalNumber(raw, 'playCount', out);
  optionalBoolean(raw, 'isTop', out);
  optionalStringArray(raw, 'images', out);
  return out;
}

/** 评论项（含归属字段：userId 可空、mine 布尔；两级评论：P1-12） */
export function validateComment(raw: unknown): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  if (!isObj(raw)) return [{ path: '', message: '不是对象' }];
  requireString(raw, 'id', out);
  requireString(raw, 'content', out);
  optionalString(raw, 'name', out);
  optionalString(raw, 'time', out);
  optionalNumber(raw, 'likes', out);
  optionalBoolean(raw, 'liked', out);
  optionalNumber(raw, 'userId', out); // null 表示无归属（历史评论）
  optionalBoolean(raw, 'mine', out);
  optionalBoolean(raw, 'isDeleted', out);
  // 两级评论：主评论 parentId/rootId 均为 null；回复的 rootId 指向主评论
  optionalString(raw, 'parentId', out);
  optionalString(raw, 'rootId', out);
  optionalNumber(raw, 'replyCount', out);
  optionalString(raw, 'replyToName', out);
  optionalNumber(raw, 'replyToUserId', out);
  // 预览回复数组：只校验形态，元素结构由 normalize 递归兜底
  if (raw.replies !== undefined && raw.replies !== null && !Array.isArray(raw.replies)) {
    out.push({ path: 'replies', message: '应为数组' });
  }
  return out;
}

/** 消息项 */
export function validateMessage(raw: unknown): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  if (!isObj(raw)) return [{ path: '', message: '不是对象' }];
  requireString(raw, 'id', out);
  requireString(raw, 'title', out);
  optionalString(raw, 'summary', out);
  optionalString(raw, 'time', out);
  optionalBoolean(raw, 'read', out);
  return out;
}

/** 输出校验告警（仅开发期调用） */
export function warnIssues(kind: string, issues: ValidationIssue[]): void {
  if (!issues.length) return;
  const detail = issues.map((i) => `${i.path || '<root>'}:${i.message}`).join('; ');
  console.warn(`[api:${kind}] 响应字段校验未通过 → ${detail}`);
}

/** 开发期开关：生产构建下跳过校验，避免额外开销 */
export const VALIDATE_RESPONSES: boolean = (() => {
  try {
    return Boolean(import.meta.env?.DEV);
  } catch {
    return false;
  }
})();
