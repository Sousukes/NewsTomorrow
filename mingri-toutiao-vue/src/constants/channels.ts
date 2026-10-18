/**
 * 资讯频道**单一来源**。
 *
 * 此前频道取值在三处各自硬编码：`types.ts` 的 `Channel` 联合类型、
 * `HomeFeed.vue` 的标签数组、`Showcase.vue` 的演示数组——新增频道要改三处，
 * 且类型与数据可能不一致。现在统一由本文件派生：
 * - `Channel` 类型由 `CHANNELS` 推导
 * - 标签栏/演示数据复用 `CHANNELS`
 */

export const CHANNELS = [
  { key: 'recommend', label: '推荐' },
  { key: 'hot', label: '热点' },
  { key: 'video', label: '视频' },
  { key: 'tech', label: '科技' },
  { key: 'finance', label: '财经' },
  { key: 'sports', label: '体育' },
  { key: 'car', label: '汽车' },
] as const;

/** 频道 key 联合类型（由 CHANNELS 推导，二者不会漂移） */
export type Channel = (typeof CHANNELS)[number]['key'];

/** 全部频道 key（顺序与 CHANNELS 一致） */
export const CHANNEL_KEYS: Channel[] = CHANNELS.map((c) => c.key);

/**
 * 关注流标识（E6b）。**刻意不是 Channel**：
 *
 * `CHANNELS` 同时被后端用于校验运营填的频道（`feeds/sources.py::CHANNEL_KEYS`），
 * 把「关注」混进去会让管理端的源配置多出一个非法选项；且关注流是**跨频道**的，
 * 与频道是正交概念（后端用 `?feed=following` 表达，不是 `?channel=following`）。
 */
export const FOLLOWING_FEED = 'following';

/** 列表键 = 真实频道 + 关注流（news store 的分桶键用这个类型） */
export type ListKey = Channel | typeof FOLLOWING_FEED;

/** 首页标签栏：关注在最左，其余是真实频道（UI 级组合，不影响 CHANNELS） */
export const HOME_TABS: ReadonlyArray<{ key: ListKey; label: string }> = [
  { key: FOLLOWING_FEED, label: '关注' },
  ...CHANNELS,
];

/** 默认频道：抓取排序与信息流的兜底取值 */
export const DEFAULT_CHANNEL: Channel = 'recommend';

const LABEL_MAP: Record<Channel, string> = Object.fromEntries(
  CHANNELS.map((c) => [c.key, c.label]),
) as Record<Channel, string>;

/** 频道 key → 中文标签；未知 key 回退为 key 本身 */
export function channelLabel(key: Channel | string): string {
  return LABEL_MAP[key as Channel] ?? key;
}

/** 类型守卫：判断任意字符串是否为合法频道（后端/URL 参数不可信时使用） */
export function isChannel(value: unknown): value is Channel {
  return typeof value === 'string' && (CHANNEL_KEYS as string[]).includes(value);
}

/**
 * 归一化频道参数：非法值回退默认频道。
 * 用于路由 query / 后端返回值等不可信输入。
 */
export function toChannel(value: unknown): Channel {
  return isChannel(value) ? value : DEFAULT_CHANNEL;
}
