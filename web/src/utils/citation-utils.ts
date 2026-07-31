import {
  Docagg,
  IReference,
  IReferenceChunk,
  IReferenceObject,
} from '@/interfaces/database/chat';

export const normalizeCitationDigits = (text: string) => {
  if (!text) return text;
  return text.replace(/[٠-٩۰-۹]/g, (char) => {
    const code = char.charCodeAt(0);
    if (code >= 0x0660 && code <= 0x0669) {
      return String.fromCharCode(code - 0x0660 + 0x30);
    }
    if (code >= 0x06f0 && code <= 0x06f9) {
      return String.fromCharCode(code - 0x06f0 + 0x30);
    }
    return char;
  });
};

export const parseCitationIndex = (value: string) => {
  const normalized = normalizeCitationDigits(value);
  const markerMatch = normalized.match(/\[(?:ID:)?(\d+)\]/);
  if (markerMatch) return Number(markerMatch[1]);
  if (/^\d+$/.test(normalized)) return Number(normalized);
  return Number.NaN;
};

export const citationMarkerReg =
  /\[(?:ID:)?([0-9\u0660-\u0669\u06F0-\u06F9]+)\]/g;

type ReferenceLike = IReference | IReferenceObject | undefined;

export const getReferenceChunks = (
  reference: ReferenceLike,
): IReferenceChunk[] => {
  const chunks = reference?.chunks ?? [];
  return Array.isArray(chunks) ? chunks : Object.values(chunks);
};

const getReferenceDocuments = (reference: ReferenceLike): Docagg[] => {
  const docs = reference?.doc_aggs ?? [];
  return Array.isArray(docs) ? docs : Object.values(docs);
};

export const hasReferenceChunk = (reference: ReferenceLike, index: number) => {
  return (
    Number.isInteger(index) &&
    index >= 0 &&
    Boolean(getReferenceChunks(reference)[index])
  );
};

export const getRenderableReferenceDocuments = (
  content: string,
  reference: ReferenceLike,
): Docagg[] => {
  const chunks = getReferenceChunks(reference);
  const docs = getReferenceDocuments(reference);
  const markerReg = new RegExp(citationMarkerReg.source, 'g');
  const matches = Array.from(
    normalizeCitationDigits(content ?? '').matchAll(markerReg),
  );

  if (matches.length === 0) return docs;

  const citedDocIds = new Set(
    matches
      .map((match) => Number(match[1]))
      .filter(
        (index) =>
          Number.isInteger(index) && index >= 0 && index < chunks.length,
      )
      .map((index) => chunks[index]?.document_id)
      .filter((documentId): documentId is string => Boolean(documentId)),
  );

  if (citedDocIds.size === 0) return [];
  return docs.filter((doc) => citedDocIds.has(doc.doc_id));
};
