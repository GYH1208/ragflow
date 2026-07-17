import { renderHook, waitFor } from '@testing-library/react';

const mockGetAuthorization = jest.fn();

jest.mock('@/utils/authorization-util', () => ({
  getAuthorization: () => mockGetAuthorization(),
}));

import { useDocumentImageUrl } from './index';

describe('useDocumentImageUrl', () => {
  const createObjectURL = jest.fn(() => 'blob:test-image');
  const revokeObjectURL = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    });
  });

  it('fetches a protected document image with the login authorization header', async () => {
    mockGetAuthorization.mockReturnValue('Bearer test-token');
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      blob: jest.fn().mockResolvedValue(new Blob(['image'])),
    });
    global.fetch = fetchMock;

    const { result } = renderHook(() => useDocumentImageUrl('image-id'));

    await waitFor(() => expect(result.current).toBe('blob:test-image'));
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/documents/images/image-id',
      { headers: { Authorization: 'Bearer test-token' } },
    );
  });

  it('uses the direct image URL when no authorization is available', () => {
    mockGetAuthorization.mockReturnValue('');
    const fetchMock = jest.fn();
    global.fetch = fetchMock;

    const { result } = renderHook(() => useDocumentImageUrl('public-image'));

    expect(result.current).toBe('/api/v1/documents/images/public-image');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
