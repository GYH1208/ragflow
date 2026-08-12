import { shouldShowApiErrorNotification } from '../request-error-policy';

describe('request error notification policy', () => {
  it('lets callers handle an API error without showing the global notification', () => {
    expect(
      shouldShowApiErrorNotification({
        skipErrorNotification: true,
      }),
    ).toBe(false);
    expect(shouldShowApiErrorNotification({})).toBe(true);
  });
});
