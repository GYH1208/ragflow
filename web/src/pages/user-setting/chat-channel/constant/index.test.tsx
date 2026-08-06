jest.mock('@/components/dynamic-form', () => ({
  FormFieldType: {
    Number: 'number',
    Password: 'password',
    Select: 'select',
    Switch: 'switch',
    Tag: 'tag',
    Text: 'text',
    Textarea: 'textarea',
  },
}));
jest.mock('@/components/svg-icon', () => ({
  __esModule: true,
  default: () => null,
}));

import { FormFieldType } from '@/components/dynamic-form';
import { ChatChannelKey, getChatChannelFields } from './index';

describe('WeCom channel fields', () => {
  const field = getChatChannelFields(ChatChannelKey.WECOM).find(
    (item: { name: string }) =>
      item.name === 'config.credential.send_pdf_reference_images',
  );

  test('defines a disabled-by-default PDF reference image switch', () => {
    expect(field).toBeDefined();
    expect(field?.type).toBe(FormFieldType.Switch);
    expect(field?.defaultValue).toBe(false);
  });

  test('shows the switch only for WebSocket connections', () => {
    expect(
      field?.shouldRender?.({
        config: { credential: { connection_type: 'websocket' } },
      }),
    ).toBe(true);
    expect(
      field?.shouldRender?.({
        config: { credential: { connection_type: 'webhook' } },
      }),
    ).toBe(false);
  });
});
