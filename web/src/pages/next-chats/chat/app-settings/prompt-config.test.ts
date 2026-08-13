import { normalizePromptConfigPdfImageSetting } from './prompt-config';

describe('normalizePromptConfigPdfImageSetting', () => {
  test.each([
    [{ system: 'Prompt' }, false],
    [{ system: 'Prompt', send_pdf_reference_images: false }, false],
    [{ system: 'Prompt', send_pdf_reference_images: 'true' }, false],
    [{ system: 'Prompt', send_pdf_reference_images: true }, true],
  ])('normalizes %p to %p', (promptConfig, expected) => {
    expect(
      normalizePromptConfigPdfImageSetting(promptConfig)
        .send_pdf_reference_images,
    ).toBe(expected);
    expect(normalizePromptConfigPdfImageSetting(promptConfig).system).toBe(
      'Prompt',
    );
  });
});
