import { Button, ButtonLoading } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useKnowledgeEntries } from '@/hooks/use-knowledge-file-request';
import { Folder, FolderOpen, Home } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface KnowledgeMoveDialogProps {
  datasetId: string;
  onClose: () => void;
  onMove: (folderId: string) => Promise<unknown>;
  loading?: boolean;
}

export function KnowledgeMoveDialog({
  datasetId,
  onClose,
  onMove,
  loading,
}: KnowledgeMoveDialogProps) {
  const { t } = useTranslation();
  const [folderId, setFolderId] = useState('');
  const query = useKnowledgeEntries({ datasetId, folderId, pageSize: 100 });
  const folders = (query.data?.entries || []).filter(
    (entry) => entry.entry_type === 'folder',
  );
  const destinationId = query.data?.parent_folder.id;

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('knowledgeFolder.moveTo')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 min-h-56 max-h-96 overflow-auto">
          {folderId && (
            <Button
              variant="ghost"
              className="w-full justify-start"
              onClick={() => setFolderId('')}
            >
              <Home className="size-4" />
              {t('knowledgeFolder.root')}
            </Button>
          )}
          {folders.map((folder) => (
            <Button
              key={folder.id}
              variant="ghost"
              className="w-full justify-start"
              onClick={() => setFolderId(folder.id)}
            >
              {folder.id === folderId ? (
                <FolderOpen className="size-4" />
              ) : (
                <Folder className="size-4" />
              )}
              {folder.name}
            </Button>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <ButtonLoading
            loading={loading}
            disabled={!destinationId}
            onClick={() => destinationId && onMove(destinationId)}
          >
            {t('knowledgeFolder.moveHere')}
          </ButtonLoading>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
