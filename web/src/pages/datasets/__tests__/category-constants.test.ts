import {
  ALL_CATEGORY,
  UNCATEGORIZED_CATEGORY,
  parseCategorySearchParam,
  toCategoryRequestFilter,
} from '../category-constants';

describe('dataset category route mapping', () => {
  it('maps virtual values without leaking all into the API', () => {
    expect(parseCategorySearchParam(null)).toBe(ALL_CATEGORY);
    expect(toCategoryRequestFilter(ALL_CATEGORY)).toBeUndefined();
    expect(toCategoryRequestFilter(UNCATEGORIZED_CATEGORY)).toBe(
      UNCATEGORIZED_CATEGORY,
    );
    expect(toCategoryRequestFilter('cat-1')).toBe('cat-1');
  });
});
