import {
  partitionUploadFiles,
  uploadKnowledgeFilesInBatches,
} from '../batch-knowledge-upload';
const files = (count: number) =>
  Array.from({ length: count }, (_, i) => new File(['x'], `${i}.txt`));
const success = (batch: File[]) => ({
  code: 0,
  data: batch.map((file) => ({ id: file.name })),
});
test('bounds count and bytes, preserving files and order', () => {
  const input = files(19);
  Object.defineProperty(input[8], 'size', { value: 40 * 1024 * 1024 });
  const batches = partitionUploadFiles(input);
  expect(batches.map((batch) => batch.length)).toEqual([8, 1, 8, 2]);
  expect(batches.flat()).toEqual(input);
  expect(batches[1][0]).toBe(input[8]);
  const large = files(3);
  large.forEach((file) =>
    Object.defineProperty(file, 'size', { value: 20 * 1024 * 1024 }),
  );
  expect(partitionUploadFiles(large).map((batch) => batch.length)).toEqual([
    1, 1, 1,
  ]);
});
test('serializes writes and reports progress before all files finish', async () => {
  const pending: (() => void)[] = [];
  let active = 0;
  let peak = 0;
  const progress = jest.fn();
  const upload = jest.fn(
    (batch: File[]) =>
      new Promise<ReturnType<typeof success>>((resolve) => {
        active++;
        peak = Math.max(peak, active);
        pending.push(() => {
          active--;
          resolve(success(batch));
        });
      }),
  );
  const result = uploadKnowledgeFilesInBatches(files(20), upload, progress);
  expect(upload).toHaveBeenCalledTimes(1);
  pending.shift()!();
  await Promise.resolve();
  await Promise.resolve();
  expect(progress).toHaveBeenCalledWith(8);
  expect(upload).toHaveBeenCalledTimes(2);
  pending.shift()!();
  await Promise.resolve();
  await Promise.resolve();
  expect(upload).toHaveBeenCalledTimes(3);
  pending.shift()!();
  const final = await result;
  expect(peak).toBe(1);
  expect(final.completed).toBe(20);
  expect(final.documents).toHaveLength(20);
  expect(final.failed).toBe(false);
});
test.each(['error-code', 'network-error', 'malformed'])(
  'stops subsequent batches on %s without retrying',
  async (failure) => {
    const upload = jest.fn(async () => {
      if (failure === 'network-error') throw Error('network');
      return failure === 'malformed' ? { code: 0 } : { code: 500 };
    });
    const result = await uploadKnowledgeFilesInBatches(
      files(32),
      upload,
      jest.fn(),
    );
    expect(upload).toHaveBeenCalledTimes(1);
    expect(result.failed).toBe(true);
    expect(result.completed).toBe(0);
  },
);
test('serializes distinct inputs that can collide after server duplicate-name allocation', async () => {
  const input = files(10);
  input[9] = new File(['other'], '0(1).txt');
  let active = 0;
  let peak = 0;
  await uploadKnowledgeFilesInBatches(
    input,
    async (batch) => {
      active++;
      peak = Math.max(peak, active);
      await Promise.resolve();
      active--;
      return success(batch);
    },
    jest.fn(),
  );
  expect(peak).toBe(1);
});
