import { renderHook } from '@testing-library/react';

import { useChatSettingSchema } from './use-chat-setting-schema';

jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));
jest.mock('@/components/llm-setting-items/next', () => ({
  LlmSettingEnabledSchema: {},
  LlmSettingFieldSchema: {},
}));
jest.mock('@/components/metadata-filter', () => ({
  MetadataFilterSchema: {},
}));
jest.mock('@/components/rerank', () => ({
  rerankFormSchema: {},
}));
jest.mock('@/components/similarity-slider', () => ({
  similarityThresholdSchema: {},
  vectorSimilarityWeightSchema: {},
}));
jest.mock('@/components/top-n-item', () => ({
  topnSchema: {},
}));

const validValues = {
  name: 'Assistant',
  icon: '',
  description: '',
  dataset_ids: [],
  prompt_config: {
    quote: true,
    keyword: false,
    tts: false,
    system: 'Use {knowledge}',
    refine_multiturn: true,
    use_kg: false,
  },
  llm_setting: {},
  top_n: 8,
  similarity_threshold: 0.2,
  vector_similarity_weight: 0.2,
  top_k: 1024,
  meta_data_filter: { method: 'disabled', manual: [] },
};

describe('chat setting PDF reference image schema', () => {
  it('accepts a missing field for old chats and boolean values', () => {
    const { result } = renderHook(() => useChatSettingSchema());

    expect(result.current.safeParse(validValues).success).toBe(true);
    expect(
      result.current.safeParse({
        ...validValues,
        prompt_config: {
          ...validValues.prompt_config,
          send_pdf_reference_images: true,
        },
      }).success,
    ).toBe(true);
  });

  it('rejects string values', () => {
    const { result } = renderHook(() => useChatSettingSchema());

    expect(
      result.current.safeParse({
        ...validValues,
        prompt_config: {
          ...validValues.prompt_config,
          send_pdf_reference_images: 'true',
        },
      }).success,
    ).toBe(false);
  });
});
