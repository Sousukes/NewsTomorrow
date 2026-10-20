/** 频道单一来源：类型与标签都由此派生，避免多处硬编码漂移。 */

export const CHANNELS = [
  { key: 'recommend', label: '推荐' },
  { key: 'hot', label: '热点' },
  { key: 'video', label: '视频' },
  { key: 'tech', label: '科技' },
  { key: 'finance', label: '财经' },
  { key: 'sports', label: '体育' },
  { key: 'car', label: '汽车' },
] as const;

/** 由 CHANNELS 推导 */
export type Channel = (typeof CHANNELS)[number]['key'];

/** 顺序与 CHANNELS 一致 */
export const CHANNEL_KEYS: Channel[] = CHANNELS.map((c) => c.key);

/**
 * 关注流标识。刻意不是 Channel：后端用它校验运营填的频道，
 * 混进去会多出非法选项；且关注流跨频道（走 ?feed=following）。
 */
export const FOLLOWING_FEED = 'following';

/** news store 的分桶键 */
export type ListKey = Channel | typeof FOLLOWING_FEED;

/** UI 级组合，不影响 CHANNELS */
export const HOME_TABS: ReadonlyArray<{ key: ListKey; label: string }> = [
  { key: FOLLOWING_FEED, label: '关注' },
  ...CHANNELS,
];

/** 兜底取值 */
export const DEFAULT_CHANNEL: Channel = 'recommend';

const LABEL_MAP: Record<Channel, string> = Object.fromEntries(
  CHANNELS.map((c) => [c.key, c.label]),
) as Record<Channel, string>;

/** 未知 key 回退为 key 本身 */
export function channelLabel(key: Channel | string): string {
  return LABEL_MAP[key as Channel] ?? key;
}

/** 类型守卫 */
export function isChannel(value: unknown): value is Channel {
  return typeof value === 'string' && (CHANNEL_KEYS as string[]).includes(value);
}

/** 非法值回退默认（路由 query 等不可信输入） */
export function toChannel(value: unknown): Channel {
  return isChannel(value) ? value : DEFAULT_CHANNEL;
}
