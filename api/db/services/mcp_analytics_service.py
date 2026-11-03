"""Read-only, process-wide MCP tool call statistics."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

KNOWN_TOOLS = (
    "ragflow_retrieval",
    "search_documents",
    "get_document_chunks",
    "get_chunk_image",
    "list_knowledge_bases",
    "list_documents",
    "export_document_catalog",
)


class MCPAnalyticsService:
    @staticmethod
    def dashboard() -> dict:
        path = Path(os.environ.get("RAGFLOW_MCP_ANALYTICS_COUNTS_FILE") or Path(__file__).resolve().parents[3] / "logs/mcp-tool-counts-9396.json")
        try:
            # Read contents and timestamp from the same file across atomic replacements.
            with path.open(encoding="utf-8") as source:
                saved = json.load(source)
                modified = os.fstat(source.fileno()).st_mtime
            if not isinstance(saved, dict) or any(not isinstance(name, str) or type(count) is not int or count < 0 for name, count in saved.items()):
                return {"available": False}
            counts = dict.fromkeys(KNOWN_TOOLS, 0)
            counts.update(saved)
            total = sum(counts.values())
            retrieval = counts["ragflow_retrieval"] + counts["search_documents"]
            content = counts["get_document_chunks"]
            return {
                "available": True,
                "total_count": total,
                "categories": {
                    "retrieval": retrieval,
                    "content": content,
                    "other": total - retrieval - content,
                },
                "tools": [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))],
                "updated_at": datetime.fromtimestamp(modified, UTC).isoformat(),
            }
        except (OSError, ValueError, UnicodeError):
            return {"available": False}
