import translationEn from '../en';
import translationZh from '../zh';

const collectLeafKeys = (
  value: Record<string, unknown>,
  prefix = '',
): string[] =>
  Object.entries(value)
    .flatMap(([key, child]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      return child !== null && typeof child === 'object'
        ? collectLeafKeys(child as Record<string, unknown>, path)
        : [path];
    })
    .sort();

describe('Simplified Chinese Admin translations', () => {
  it('contains every English Admin translation key', () => {
    expect(collectLeafKeys(translationZh.translation.admin)).toEqual(
      collectLeafKeys(translationEn.translation.admin),
    );
  });

  it('uses Simplified Chinese for core Admin navigation', () => {
    expect(translationZh.translation.admin.serviceStatus).toBe('服务状态');
    expect(translationZh.translation.admin.userManagement).toBe('用户管理');
    expect(translationZh.translation.admin.sandboxSettings).toBe('沙箱设置');
  });
});
