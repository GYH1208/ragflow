export type ApiErrorNotificationOptions = {
  skipErrorNotification?: boolean;
  [key: string]: unknown;
};

export function shouldShowApiErrorNotification(
  options: ApiErrorNotificationOptions,
) {
  return options.skipErrorNotification !== true;
}
