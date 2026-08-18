import { Button } from '@/components/ui/button';
import { KnowledgeFolderAncestor } from '@/interfaces/database/knowledge-file';
import { ChevronRight, Home } from 'lucide-react';

interface KnowledgeFileBreadcrumbProps {
  ancestors: KnowledgeFolderAncestor[];
  rootName?: string;
  onNavigate: (folderId: string) => void;
}

export function KnowledgeFileBreadcrumb({
  ancestors,
  rootName,
  onNavigate,
}: KnowledgeFileBreadcrumbProps) {
  if (!ancestors.length) {
    return (
      <div className="flex items-center gap-1 text-sm text-text-secondary">
        <Home className="size-4" />
        <span>{rootName}</span>
      </div>
    );
  }

  return (
    <nav
      aria-label="Knowledge folder breadcrumb"
      className="flex items-center gap-1"
    >
      {ancestors.map((ancestor, index) => (
        <div key={ancestor.id} className="flex items-center gap-1">
          {index > 0 && <ChevronRight className="size-4 text-text-disabled" />}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            onClick={() => onNavigate(ancestor.id)}
          >
            {index === 0 && <Home className="size-4" />}
            {ancestor.name}
          </Button>
        </div>
      ))}
    </nav>
  );
}
