import type { StoryObj } from '@storybook/vue3-vite';
import ChannelTabs from './ChannelTabs.vue';
import type { ChannelTab } from '@/types';

const tabs: ChannelTab[] = [
  { key: 'recommend', label: '推荐', active: true },
  { key: 'hot', label: '热点' },
  { key: 'video', label: '视频' },
  { key: 'tech', label: '科技' },
  { key: 'finance', label: '财经' },
];

const meta = {
  title: 'Components/ChannelTabs',
  component: ChannelTabs,
  args: { tabs, active: 'recommend' },
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
