import type { StoryObj } from '@storybook/vue3-vite';
import NewsCard from './NewsCard.vue';
import { mockNews } from '@/data/mockNews';

const byVariant = (v: string) => mockNews.find((n) => n.variant === v)!;

const meta = {
  title: 'Components/NewsCard',
  component: NewsCard,
};
export default meta;
type Story = StoryObj<typeof meta>;

/** 四种变体：纯文字 / 左文右图 / 三图 / 视频 */
export const Text: Story = { args: { item: byVariant('text') } };
export const LeftRight: Story = { args: { item: byVariant('left-right') } };
export const Triple: Story = { args: { item: byVariant('triple') } };
export const Video: Story = { args: { item: byVariant('video') } };
