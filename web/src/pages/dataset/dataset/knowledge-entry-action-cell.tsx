import { Button } from '@/components/ui/button';
import { KnowledgeEntry } from '@/interfaces/database/knowledge-file';
import { downloadDatasetDocument } from '@/services/file-manager-service';
import { downloadFileFromBlob } from '@/utils/file-util';
import { Download, FolderInput, PenLine, Trash2 } from 'lucide-react';
import * as React from 'react';

interface KnowledgeEntryActionCellProps {
  entry: KnowledgeEntry;
  onRename: (entry: KnowledgeEntry) => void;
  onMove: (entry: KnowledgeEntry) => void;
  onDelete: (entry: KnowledgeEntry) => void;
}

export function KnowledgeEntryActionCell({
  entry,
  onRename,
  onMove,
  onDelete,
}: KnowledgeEntryActionCellProps) {
  const download = async () => {
    if (entry.entry_type !== 'document') return;
    const extension = entry.name.split('.').pop()?.toLowerCase() || 'bin';
    const response = await downloadDatasetDocument({
      datasetId: entry.dataset_id,
      docId: entry.id,
      ext: extension,
    });
    downloadFileFromBlob(new Blob([response.data]), entry.name);
  };

  return (
    <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
      <Button size="icon-xs" variant="ghost" onClick={() => onRename(entry)}>
        <PenLine className="size-4" />
      </Button>
      <Button size="icon-xs" variant="ghost" onClick={() => onMove(entry)}>
        <FolderInput className="size-4" />
      </Button>
      {entry.entry_type === 'document' && (
        <Button size="icon-xs" variant="ghost" onClick={download}>
          <Download className="size-4" />
        </Button>
      )}
      <Button size="icon-xs" variant="ghost" onClick={() => onDelete(entry)}>
        <Trash2 className="size-4" />
      </Button>
    </div>
  );
}
