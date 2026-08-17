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
    const relativePath = (file as FolderUploadFile).webkitRelativePath || '';
    const uploadFilename = relativePath
      ? relativePath.split(/[\\/]/).pop() || file.name
      : file.name;
    formData.append('file', file, uploadFilename);
    formData.append('relative_path', relativePath);
  });
  if (parserConfig) {
    formData.append('parser_config', JSON.stringify(parserConfig));
  }
  return formData;
}
