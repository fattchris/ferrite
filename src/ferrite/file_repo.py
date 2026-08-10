"""File repository — stores original source content on disk.

When an agent ingests content (arxiv paper, web page, code, transcript),
the raw content is saved as a file in the repository. The file path is
stored in the Episode node in Neo4j, so facts always link back to their
original source.

Directory structure:
    {FILE_REPO_PATH}/
    ├── arxiv/          # arxiv papers
    │   └── 2401.12345.txt
    ├── web/            # web pages
    │   └── {sha256_8}.html
    ├── text/           # plain text / transcripts
    │   └── {sha256_8}.txt
    ├── code/           # code files
    │   └── {sha256_8}.py
    └── _index.json     # {file_id: {path, episode_id, content_type, source, saved_at}}

File naming: content-hash based for dedup. Same content → same file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory index (loaded on first access)
_index: dict[str, dict] | None = None
_index_path: Path | None = None


def _repo_root() -> Path:
    """Get the repository root from settings."""
    from .config import get_settings
    return Path(get_settings().FILE_REPO_PATH)


def _index_file() -> Path:
    """Get the path to the index file."""
    global _index_path
    if _index_path is None:
        _index_path = _repo_root() / "_index.json"
    return _index_path


def _load_index() -> dict[str, dict]:
    """Load the file index from disk (cached)."""
    global _index
    if _index is not None:
        return _index
    idx_path = _index_file()
    if idx_path.exists():
        try:
            _index = json.loads(idx_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load file index, starting fresh: {e}")
            _index = {}
    else:
        _index = {}
    return _index


def _save_index() -> None:
    """Persist the index to disk."""
    if _index is None:
        return
    idx_path = _index_file()
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(json.dumps(_index, indent=2, default=str))


def _content_hash(content: str | bytes) -> str:
    """SHA-256 of content, first 16 chars for filename."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]


def _ext_for_type(content_type: str, source: dict | None = None) -> str:
    """Determine file extension from content type and source metadata."""
    # Check source for hints
    if source:
        src_type = source.get("type", "")
        if "arxiv" in src_type:
            return ".txt"
        if "url" in source or source.get("url"):
            url = source.get("url", "")
            if ".pdf" in url:
                return ".pdf"
            if ".html" in url:
                return ".html"
        if source.get("filename"):
            fname = source["filename"]
            if "." in fname:
                return Path(fname).suffix

    # Fallback to content_type
    ext_map = {
        "text": ".txt",
        "code": ".py",  # default to .py, override via source
        "pdf": ".pdf",
        "web": ".html",
        "voice": ".txt",
        "json": ".json",
    }
    return ext_map.get(content_type, ".txt")


def _subdir_for_type(content_type: str, source: dict | None = None) -> str:
    """Determine subdirectory based on content type and source."""
    if source:
        src_type = source.get("type", "")
        if "arxiv" in src_type:
            return "arxiv"
        if "web" in src_type or "url" in source:
            return "web"
        if "voice" in src_type or "transcript" in src_type:
            return "transcripts"
        if "code" in src_type:
            return "code"

    type_map = {
        "text": "text",
        "code": "code",
        "pdf": "documents",
        "web": "web",
        "voice": "transcripts",
        "json": "json",
    }
    return type_map.get(content_type, "text")


def save_content(
    content: str,
    content_type: str = "text",
    source: dict | None = None,
    episode_id: str | None = None,
) -> str:
    """Save content to the file repository.

    Returns the relative file path (e.g., "arxiv/2401.12345.txt").
    If content already exists (same hash), returns existing path.
    """
    root = _repo_root()

    # Generate content hash for dedup
    chash = _content_hash(content)
    subdir = _subdir_for_type(content_type, source)
    ext = _ext_for_type(content_type, source)

    # Special naming for arxiv papers
    if source and "arxiv" in source.get("type", ""):
        # Use arxiv ID if available
        arxiv_id = source.get("arxiv_id") or source.get("id", "")
        if arxiv_id:
            # Clean: remove "arxiv:" prefix if present
            arxiv_id = arxiv_id.replace("arxiv:", "").replace("/", "_")
            filename = f"{arxiv_id}{ext}"
        else:
            filename = f"{chash}{ext}"
    elif source and source.get("filename"):
        filename = source["filename"]
    else:
        filename = f"{chash}{ext}"

    rel_path = f"{subdir}/{filename}"
    abs_path = root / subdir / filename

    # Check if already exists (dedup)
    idx = _load_index()
    for file_id, entry in idx.items():
        if entry.get("path") == rel_path:
            # Already saved — just link the episode_id if different
            if episode_id and episode_id not in entry.get("episode_ids", []):
                entry.setdefault("episode_ids", []).append(episode_id)
                _save_index()
            logger.debug(f"File already in repo: {rel_path}")
            return rel_path

    # Write file
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")
    logger.info(f"Saved source file: {rel_path}")

    # Update index
    file_id = chash
    idx[file_id] = {
        "path": rel_path,
        "episode_ids": [episode_id] if episode_id else [],
        "content_type": content_type,
        "source": source or {},
        "size_bytes": len(content.encode("utf-8")),
        "saved_at": datetime.utcnow().isoformat(),
    }
    _save_index()

    return rel_path


def get_content(rel_path: str) -> Optional[str]:
    """Read file content from the repository by relative path."""
    root = _repo_root()
    abs_path = root / rel_path
    if not abs_path.exists():
        logger.warning(f"File not found in repo: {rel_path}")
        return None
    return abs_path.read_text(encoding="utf-8")


def get_file_path(rel_path: str) -> Optional[Path]:
    """Get the absolute filesystem path for a relative repo path."""
    root = _repo_root()
    abs_path = root / rel_path
    if abs_path.exists():
        return abs_path
    return None


def find_by_episode(episode_id: str) -> Optional[str]:
    """Find the source file path for a given episode ID."""
    idx = _load_index()
    for file_id, entry in idx.items():
        if episode_id in entry.get("episode_ids", []):
            return entry.get("path")
    return None


def list_files(
    content_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List files in the repository, optionally filtered by content type."""
    idx = _load_index()
    results = []
    for file_id, entry in idx.items():
        if content_type and entry.get("content_type") != content_type:
            continue
        results.append({
            "file_id": file_id,
            **entry,
        })
    # Sort by saved_at descending
    results.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
    return results[offset:offset + limit]


def delete_file(rel_path: str) -> bool:
    """Delete a file from the repository."""
    root = _repo_root()
    abs_path = root / rel_path
    if abs_path.exists():
        abs_path.unlink()
        # Remove from index
        idx = _load_index()
        to_remove = []
        for file_id, entry in idx.items():
            if entry.get("path") == rel_path:
                to_remove.append(file_id)
        for fid in to_remove:
            del idx[fid]
        _save_index()
        logger.info(f"Deleted source file: {rel_path}")
        return True
    return False
