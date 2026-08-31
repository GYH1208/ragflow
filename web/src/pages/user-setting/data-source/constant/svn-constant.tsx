import { FormFieldType } from '@/components/dynamic-form';
import { TFunction } from 'i18next';

export const svnConstant = (t: TFunction) => [
  {
    label: t('setting.svnRepositoryUrl'),
    name: 'config.repository_url',
    type: FormFieldType.Text,
    required: true,
    placeholder: 'https://svn.example.com/svn/company',
  },
  {
    label: t('setting.svnBasePath'),
    name: 'config.base_path',
    type: FormFieldType.Text,
    required: true,
  },
  {
    label: t('setting.svnIncludeRoots'),
    name: 'config.include_roots',
    type: FormFieldType.Tag,
    required: true,
  },
  {
    label: t('setting.svnExcludeTerms'),
    name: 'config.exclude_name_contains',
    type: FormFieldType.Tag,
    required: false,
  },
  {
    label: t('setting.svnUsername'),
    name: 'config.credentials.username',
    type: FormFieldType.Text,
    required: true,
  },
  {
    label: t('setting.svnPassword'),
    name: 'config.credentials.password',
    type: FormFieldType.Password,
    required: true,
  },
  {
    label: t('setting.svnBatchSize'),
    name: 'config.batch_size',
    type: FormFieldType.Number,
    required: false,
    validation: { min: 1, message: 'Batch size must be at least 1' },
  },
];
