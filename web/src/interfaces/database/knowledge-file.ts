import { IDocumentInfo } from './document';

export interface KnowledgeFolderEntry {
  entry_type: 'folder';
  id: string;
  file_id: string;
  parent_id: string;
  name: string;
  type: 'folder';
  relative_path: string;
  has_child_folder: boolean;
  size: number;
  create_time: number;
  update_time: number;
}

export type KnowledgeDocumentEntry = IDocumentInfo & {
  entry_type: 'document';
  file_id: string;
  parent_id: string;
  relative_path: string;
  token_count?: number;
};

export type KnowledgeEntry = KnowledgeFolderEntry | KnowledgeDocumentEntry;

export interface KnowledgeEntriesResult {
  entries: KnowledgeEntry[];
  parent_folder: { id: string; name: string };
  total: number;
}

export interface KnowledgeFolderAncestor {
  id: string;
  parent_id: string;
  name: string;
}

export interface KnowledgeDeleteResult {
  deleted: number;
  failed: Array<{ id: string; path: string; message: string }>;
}
