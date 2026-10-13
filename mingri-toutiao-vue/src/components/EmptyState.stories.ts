import type { StoryObj } from '@storybook/vue3-vite';
import EmptyState from './EmptyState.vue';

const meta = {
  title: 'Components/EmptyState',
  component: EmptyState,
  argTypes: {
    icon: {
      control: 'select',
      options: ['favorite', 'history', 'message', 'search', 'network', 'collect'],
    },
  },
  args: {
    title: '还没有收藏的内容',
    description: '在资讯详情页点击收藏，喜欢的内容会保存在这里',
    icon: 'favorite',
  },
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Favorite: Story = {};
export const History: Story = { args: { icon: 'history', title: '暂无浏览历史' } };
export const Network: Story = { args: { icon: 'network', title: '网络开小差了' } };
