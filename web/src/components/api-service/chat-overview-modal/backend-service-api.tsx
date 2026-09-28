import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

import { CopyToClipboardWithText } from '@/components/copy-to-clipboard';
import { useTranslate } from '@/hooks/common-hooks';

export const canManageApiKeys = (
  tenantInfo?: { role?: string },
  userInfo?: { is_superuser?: boolean },
) =>
  tenantInfo?.role === 'owner' ||
  tenantInfo?.role === 'admin' ||
  userInfo?.is_superuser === true;

const BackendServiceApi = ({
  show,
  canManageKeys = true,
}: {
  show(): void;
  canManageKeys?: boolean;
}) => {
  const { t } = useTranslate('chat');

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-4">
          <CardTitle>RAGFlow API</CardTitle>
          {canManageKeys && <Button onClick={show}>{t('apiKey')}</Button>}
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2">
          <b className="font-semibold">{t('backendServiceApi')}</b>
          <CopyToClipboardWithText
            text={location.origin}
          ></CopyToClipboardWithText>
        </div>
      </CardContent>
    </Card>
  );
};

export default BackendServiceApi;
