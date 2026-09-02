jest.mock('@/components/dynamic-form', () => ({
  FilterFormField: 'filter',
  FormFieldType: new Proxy(
    {},
    { get: (_target, property) => String(property) },
  ),
}));
jest.mock('@/components/icon-font', () => ({
  IconFontFill: () => null,
}));
jest.mock('@/components/svg-icon', () => () => null);
jest.mock('../component/box-token-field', () => () => null);
jest.mock('../component/gmail-token-field', () => () => null);
jest.mock('../component/google-drive-token-field', () => () => null);

import { FormFieldType } from '@/components/dynamic-form';
import {
  DataSourceFormDefaultValues,
  DataSourceFormFields,
  DataSourceKey,
} from '../constant';

describe('SVN data source', () => {
  it('builds a safe SVN connector configuration with hourly defaults', () => {
    const values = DataSourceFormDefaultValues[DataSourceKey.SVN];
    const fields = DataSourceFormFields[DataSourceKey.SVN];
    const password = fields.find(
      (field) => field.name === 'config.credentials.password',
    );
    const generateFileIndex = fields.find(
      (field) => field.name === 'config.generate_file_index',
    );
    const fileUrlBase = fields.find(
      (field) => field.name === 'config.file_url_base',
    );

    expect(values.refresh_freq).toBe(60);
    expect(values.prune_freq).toBe(60);
    expect(values.config.include_roots).toEqual([
      '1、一级文件',
      '2、二级文件',
      '3、三级文件',
      '4、四级文件',
    ]);
    expect(values.config.exclude_name_contains).toEqual(['旧版']);
    expect(values.config.generate_file_index).toBe(false);
    expect(values.config.file_url_base).toBe('');
    expect(password?.type).toBe(FormFieldType.Password);
    expect(generateFileIndex?.type).toBe(FormFieldType.Checkbox);
    expect(fileUrlBase?.type).toBe(FormFieldType.Text);
  });
});
