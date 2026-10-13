import type { StoryObj } from '@storybook/vue3-vite';
import BannerCarousel from './BannerCarousel.vue';
import { mockNews } from '@/data/mockNews';

const meta = {
  title: 'Components/BannerCarousel',
  component: BannerCarousel,
  args: {
    items: mockNews.slice(0, 3),
    interval: 4000,
  },
  decorators: [
    () => ({
      template: '<div style="max-width:390px"><story /></div>',
    }),
  ],
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Autoplay: Story = {};
export const ManualOnly: Story = { args: { interval: 0 } };
