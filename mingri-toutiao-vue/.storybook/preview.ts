import type { Preview } from '@storybook/vue3-vite';

// 引入设计令牌与全局样式，保证 stories 与真实应用一致
import '../src/styles/tokens.css';
import '../src/styles/global.css';

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
  },
};

export default preview;
