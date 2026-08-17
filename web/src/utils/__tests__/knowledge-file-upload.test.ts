import {
  buildKnowledgeUploadFormData,
  getUploadDisplayPath,
} from '../knowledge-file-upload';

function folderFile(name: string, relativePath: string) {
  const file = new File(['content'], name, { type: 'text/plain' });
  Object.defineProperty(file, 'webkitRelativePath', { value: relativePath });
  return file;
}

test('appends one ordered relative_path for every file', () => {
  const files = [
    folderFile('A.txt', '中文目录/制度/A.txt'),
    folderFile('B.txt', '中文目录/表单/B.txt'),
  ];

  const formData = buildKnowledgeUploadFormData(files);

  expect(formData.getAll('file')).toEqual(files);
  expect(formData.getAll('relative_path')).toEqual([
    '中文目录/制度/A.txt',
    '中文目录/表单/B.txt',
  ]);
});

test('uses an empty relative path for a normal file', () => {
  const file = new File(['content'], 'single.txt');
  expect(buildKnowledgeUploadFormData([file]).getAll('relative_path')).toEqual([
    '',
  ]);
});

test('uses the relative path basename as the multipart filename', () => {
  const file = folderFile('caf\u00e9.txt', 'documents/cafe\u0301.txt');

  const uploadedFile = buildKnowledgeUploadFormData([file]).get('file') as File;

  expect(uploadedFile.name).toBe('cafe\u0301.txt');
});

test('shows the relative path in the upload preview', () => {
  expect(getUploadDisplayPath(folderFile('A.txt', '中文目录/制度/A.txt'))).toBe(
    '中文目录/制度/A.txt',
  );
});
