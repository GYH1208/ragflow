import React from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { changeLanguageAsync, supportedLanguages } from '@/locales/config';

const AdminLanguageSwitcher = () => {
  const { t, i18n } = useTranslation();
  const languageCode = i18n.resolvedLanguage || i18n.language;
  const currentLanguage = supportedLanguages.find(
    ({ code }) => code === languageCode,
  );

  return React.createElement(
    DropdownMenu,
    null,
    <DropdownMenuTrigger asChild>
      <Button
        type="button"
        variant="ghost"
        aria-label={t('admin.language')}
        className="justify-start"
      >
        {currentLanguage?.displayName || languageCode}
      </Button>
    </DropdownMenuTrigger>,
    <DropdownMenuContent align="start">
      {supportedLanguages.map(({ code, displayName }) => (
        <DropdownMenuItem
          key={code}
          onClick={() => void changeLanguageAsync(code)}
        >
          {displayName}
        </DropdownMenuItem>
      ))}
    </DropdownMenuContent>,
  );
};

export default AdminLanguageSwitcher;
