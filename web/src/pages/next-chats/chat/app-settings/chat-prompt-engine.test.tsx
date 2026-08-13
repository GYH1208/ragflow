import { render } from '@testing-library/react';
import { FormProvider, useForm } from 'react-hook-form';

import { TooltipProvider } from '@/components/ui/tooltip';

import { ChatPromptEngine } from './chat-prompt-engine';

const React = jest.requireActual<typeof import('react')>('react');
(globalThis as any).React = React;

const renderedSwitches: Array<{
  name: string;
  label: unknown;
  tooltip: unknown;
}> = [];

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
jest.mock('@/components/switch-fom-field', () => ({
  SwitchFormField: (props: {
    name: string;
    label: unknown;
    tooltip: unknown;
  }) => {
    renderedSwitches.push(props);
    return null;
  },
}));
jest.mock('@/components/collapse', () => ({
  Collapse: ({ children }: React.PropsWithChildren) => <>{children}</>,
}));
jest.mock('@/components/cross-language-form-field', () => ({
  CrossLanguageFormField: () => null,
}));
jest.mock('@/components/metadata-filter', () => ({
  MetadataFilter: () => null,
}));
jest.mock('@/components/ui/multi-select', () => ({
  MultiSelect: () => null,
}));
jest.mock('@/components/rerank', () => ({
  RerankFormFields: () => null,
}));
jest.mock('@/components/similarity-slider', () => ({
  SimilaritySliderFormField: () => null,
}));
jest.mock('@/components/tavily-form-field', () => ({
  TavilyFormField: () => null,
}));
jest.mock('@/components/toc-enhance-form-field', () => ({
  TOCEnhanceFormField: () => null,
}));
jest.mock('@/components/top-n-item', () => ({
  TopNFormField: () => null,
}));
jest.mock('@/components/use-knowledge-graph-item', () => ({
  UseKnowledgeGraphFormField: () => null,
}));
jest.mock('@/hooks/use-knowledge-request', () => ({
  useFetchKnowledgeMetadataKeys: () => ({ data: [], loading: false }),
}));
jest.mock('./dynamic-variable', () => ({
  DynamicVariableForm: () => null,
}));

function Harness() {
  const form = useForm({
    defaultValues: {
      dataset_ids: [],
      prompt_config: {
        empty_response: '',
        system: '',
        reference_metadata: { include: false },
      },
    },
  });
  return (
    <TooltipProvider>
      <FormProvider {...form}>
        <ChatPromptEngine />
      </FormProvider>
    </TooltipProvider>
  );
}

describe('ChatPromptEngine PDF reference image switch', () => {
  beforeEach(() => {
    renderedSwitches.length = 0;
  });

  it('binds the approved copy to the prompt-config field', () => {
    render(<Harness />);

    expect(renderedSwitches).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: 'prompt_config.send_pdf_reference_images',
          label: 'chat.sendPdfReferenceImages',
          tooltip: 'chat.sendPdfReferenceImagesTip',
        }),
      ]),
    );
  });
});
