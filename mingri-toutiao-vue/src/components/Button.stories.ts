import type { StoryObj } from '@storybook/vue3-vite';
import Button from './Button.vue';

const meta = {
  title: 'Components/Button',
  component: Button,
  argTypes: {
    variant: { control: 'select', options: ['primary', 'secondary', 'text'] },
    size: { control: 'select', options: ['md', 'lg'] },
  },
  args: { label: '按钮' },
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Primary: Story = { args: { variant: 'primary' } };
export const Secondary: Story = { args: { variant: 'secondary' } };
export const Text: Story = { args: { variant: 'text' } };
export const Disabled: Story = { args: { variant: 'primary', disabled: true } };
export const LargeBlock: Story = { args: { variant: 'primary', size: 'lg', block: true } };
