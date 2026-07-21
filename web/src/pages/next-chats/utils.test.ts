import { MessageType } from '@/constants/chat';
import { IMessage, IReference } from '@/interfaces/database/chat';
import { buildMessageItemReference } from './utils';

const createMessage = (
  role: MessageType,
  id: string,
  content = id,
): IMessage => ({ role, id, content });

const createReference = (documentId: string): IReference => ({
  chunks: [],
  doc_aggs: [
    {
      count: 1,
      doc_id: documentId,
      doc_name: `${documentId}.docx`,
    },
  ],
  total: 1,
});

describe('buildMessageItemReference', () => {
  it('skips an unreferenced assistant prologue in a regular chat', () => {
    const answer = createMessage(MessageType.Assistant, 'answer');
    const reference = createReference('answer-document');

    expect(
      buildMessageItemReference(
        {
          messages: [
            createMessage(MessageType.Assistant, 'prologue'),
            createMessage(MessageType.User, 'question'),
            answer,
          ],
          reference: [reference],
        },
        answer,
      ),
    ).toBe(reference);
  });

  it('does not skip the first assistant answer in a channel chat', () => {
    const firstAnswer = createMessage(MessageType.Assistant, 'answer-1');
    const secondAnswer = createMessage(MessageType.Assistant, 'answer-2');
    const firstReference = createReference('document-1');
    const secondReference = createReference('document-2');
    const conversation = {
      messages: [
        createMessage(MessageType.User, 'question-1'),
        firstAnswer,
        createMessage(MessageType.User, 'question-2'),
        secondAnswer,
      ],
      reference: [firstReference, secondReference],
    };

    expect(buildMessageItemReference(conversation, firstAnswer)).toBe(
      firstReference,
    );
    expect(buildMessageItemReference(conversation, secondAnswer)).toBe(
      secondReference,
    );
  });

  it('does not skip a referenced first assistant message', () => {
    const answer = createMessage(MessageType.Assistant, 'answer');
    const reference = createReference('answer-document');

    expect(
      buildMessageItemReference(
        { messages: [answer], reference: [reference] },
        answer,
      ),
    ).toBe(reference);
  });
});
