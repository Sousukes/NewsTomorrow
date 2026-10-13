import type { StoryObj } from '@storybook/vue3-vite';
import AppNav from './AppNav.vue';

const meta = {
  title: 'Components/AppNav',
  component: AppNav,
  argTypes: {
    variant: { control: 'select', options: ['title', 'brand', 'search', 'detail'] },
  },
  args: { variant: 'title', title: '我的收藏' },
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Title: Story = { args: { variant: 'title', title: '我的收藏', showBack: true } };
export const Brand: Story = { args: { variant: 'brand', showSearch: true, showMessage: true } };
export const Search: Story = { args: { variant: 'search' } };
export const Detail: Story = {
  args: {
    variant: 'detail',
    author: { name: '新华社', time: '2小时前' },
    showBack: true,
    showMore: true,
  },
};
