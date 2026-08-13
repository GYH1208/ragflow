export function normalizePromptConfigPdfImageSetting<
  T extends Record<string, unknown>,
>(promptConfig: T) {
  return {
    ...promptConfig,
    send_pdf_reference_images:
      promptConfig.send_pdf_reference_images === true,
  };
}
