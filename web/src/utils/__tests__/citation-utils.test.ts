import {
  IReference,
  IReferenceChunk,
  IReferenceObject,
} from '@/interfaces/database/chat';
import {
  getRenderableReferenceDocuments,
  hasReferenceChunk,
} from '../citation-utils';

const makeChunk = (id: string, documentId: string): IReferenceChunk => ({
  id,
  content: null,
  document_id: documentId,
  document_name: `${documentId}.docx`,
  dataset_id: 'dataset-1',
  image_id: '',
  similarity: 0.9,
  vector_similarity: 0.9,
  term_similarity: 0.9,
  positions: [],
});

const reference: IReference = {
  chunks: [
    makeChunk('chunk-0', 'doc-a'),
    makeChunk('chunk-1', 'doc-a'),
    makeChunk('chunk-2', 'doc-b'),
  ],
  doc_aggs: [
    { doc_id: 'doc-a', doc_name: 'A.docx', count: 2 },
    { doc_id: 'doc-b', doc_name: 'B.docx', count: 1 },
    { doc_id: 'candidate-only', doc_name: '无关.docx', count: 8 },
  ],
  total: 70,
};

describe('chat reference consistency', () => {
  it('keeps only documents referenced by valid markers', () => {
    expect(
      getRenderableReferenceDocuments('依据 [ID:0][ID:2]。', reference),
    ).toEqual([
      { doc_id: 'doc-a', doc_name: 'A.docx', count: 2 },
      { doc_id: 'doc-b', doc_name: 'B.docx', count: 1 },
    ]);
  });

  it('returns no documents when every marker is out of range', () => {
    expect(
      getRenderableReferenceDocuments('错误 [ID:42][ID:43]。', reference),
    ).toEqual([]);
  });

  it('keeps valid documents when markers are mixed', () => {
    expect(
      getRenderableReferenceDocuments('有效 [ID:1]，无效 [ID:42]。', reference),
    ).toEqual([{ doc_id: 'doc-a', doc_name: 'A.docx', count: 2 }]);
  });

  it('preserves backend doc_aggs when the message has no markers', () => {
    expect(
      getRenderableReferenceDocuments('没有行内引用的历史消息。', reference),
    ).toEqual(reference.doc_aggs);
  });

  it('recognizes array and record chunk containers', () => {
    expect(hasReferenceChunk(reference, 2)).toBe(true);
    expect(hasReferenceChunk(reference, 42)).toBe(false);

    const objectReference: IReferenceObject = {
      chunks: { 0: reference.chunks[0] },
      doc_aggs: { 0: reference.doc_aggs[0] },
    };
    expect(hasReferenceChunk(objectReference, 0)).toBe(true);
  });
});
