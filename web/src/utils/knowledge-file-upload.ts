export type FolderUploadFile = File & { webkitRelativePath?: string };

export function getUploadDisplayPath(file: File): string {
  return (file as FolderUploadFile).webkitRelativePath || file.name;
}

export function buildKnowledgeUploadFormData(
  files: File[],
  parserConfig?: Record<string, unknown>,
): FormData {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append('file', file);
    formData.append(
      'relative_path',
      (file as FolderUploadFile).webkitRelativePath || '',
    );
  });
  if (parserConfig) {
    formData.append('parser_config', JSON.stringify(parserConfig));
  }
  return formData;
}
