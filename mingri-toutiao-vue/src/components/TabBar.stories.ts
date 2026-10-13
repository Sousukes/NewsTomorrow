import type { StoryObj } from '@storybook/vue3-vite';
import TabBar from './TabBar.vue';
import type { TabItem } from '@/types';

const items: TabItem[] = [
  { key: 'home', label: '首页', icon: 'home' },
  { key: 'video', label: '视频', icon: 'video' },
  { key: 'message', label: '消息', icon: 'message' },
  { key: 'mine', label: '我的', icon: 'mine' },
];

const meta = {
  title: 'Components/TabBar',
  component: TabBar,
  args: { items, active: 'home' },
  decorators: [
    () => ({
      template: '<div style="max-width:390px"><story /></div>',
    }),
  ],
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const ActiveVideo: Story = { args: { active: 'video' } };
