import { describe, expect, it } from 'vitest';
import {
  CHANNELS,
  CHANNEL_KEYS,
  DEFAULT_CHANNEL,
  channelLabel,
  isChannel,
  toChannel,
} from '@/constants/channels';

describe('constants/channels', () => {
  it('频道 key 唯一', () => {
    expect(new Set(CHANNEL_KEYS).size).toBe(CHANNELS.length);
  });

  it('每个频道都有非空 key 与中文标签', () => {
    for (const c of CHANNELS) {
      expect(c.key).toBeTruthy();
      expect(c.label).toBeTruthy();
    }
  });

  it('CHANNEL_KEYS 顺序与 CHANNELS 一致', () => {
    expect(CHANNEL_KEYS).toEqual(CHANNELS.map((c) => c.key));
  });

  it('默认频道是合法成员', () => {
    expect(CHANNEL_KEYS).toContain(DEFAULT_CHANNEL);
  });

  describe('channelLabel', () => {
    it('返回对应中文标签', () => {
      expect(channelLabel('tech')).toBe('科技');
      expect(channelLabel('finance')).toBe('财经');
    });

    it('未知 key 回退为 key 本身（不返回 undefined）', () => {
      expect(channelLabel('unknown' as never)).toBe('unknown');
    });
  });

  describe('isChannel / toChannel', () => {
    it('识别合法频道', () => {
      for (const k of CHANNEL_KEYS) expect(isChannel(k)).toBe(true);
    });

    it('拒绝非法输入（含 null / 数字 / 空串）', () => {
      for (const v of ['nope', '', null, undefined, 1, {}, []]) {
        expect(isChannel(v)).toBe(false);
      }
    });

    it('toChannel 归一化：非法值回退默认频道', () => {
      expect(toChannel('tech')).toBe('tech');
      expect(toChannel('?channel=1')).toBe(DEFAULT_CHANNEL);
      expect(toChannel(undefined)).toBe(DEFAULT_CHANNEL);
    });
  });
});
