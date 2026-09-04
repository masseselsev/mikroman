import difflib
import re
from typing import List, Optional

from pydantic import BaseModel

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class DiffLine(BaseModel):
    type: str  # "add", "del", "ctx"
    content: str
    old_line_no: Optional[int] = None
    new_line_no: Optional[int] = None


class DiffHunk(BaseModel):
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: List[DiffLine]


class DiffResult(BaseModel):
    base_id: Optional[int] = None
    target_id: Optional[int] = None
    is_target_live: bool = False
    lines_added: int = 0
    lines_removed: int = 0
    total_changes: int = 0
    hunks: List[DiffHunk] = []
    raw_unified: str = ""


class DiffEngine:
    @staticmethod
    def diff_texts(
        base_text: str,
        target_text: str,
        fromfile: str = "base.rsc",
        tofile: str = "target.rsc",
        context_lines: int = 3,
        base_id: Optional[int] = None,
        target_id: Optional[int] = None,
        is_target_live: bool = False,
    ) -> DiffResult:
        base_lines = base_text.splitlines(keepends=True)
        target_lines = target_text.splitlines(keepends=True)

        raw_diff_lines = list(
            difflib.unified_diff(
                base_lines, target_lines, fromfile=fromfile, tofile=tofile, n=context_lines
            )
        )
        if not raw_diff_lines:
            return DiffResult(
                base_id=base_id,
                target_id=target_id,
                is_target_live=is_target_live,
                lines_added=0,
                lines_removed=0,
                total_changes=0,
                hunks=[],
                raw_unified="",
            )

        raw_unified = "".join(raw_diff_lines)
        hunks: List[DiffHunk] = []
        current_hunk: Optional[DiffHunk] = None
        curr_old = 0
        curr_new = 0
        lines_added = 0
        lines_removed = 0

        for line in raw_diff_lines:
            if line.startswith("--- ") or line.startswith("+++ "):
                continue
            m = HUNK_HEADER_RE.match(line)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2) or 1)
                new_start = int(m.group(3))
                new_count = int(m.group(4) or 1)
                curr_old = old_start
                curr_new = new_start
                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    header=line.strip(),
                    lines=[],
                )
                hunks.append(current_hunk)
                continue

            if current_hunk is None:
                continue

            content = line[1:].rstrip("\r\n")
            if line.startswith("+"):
                lines_added += 1
                current_hunk.lines.append(
                    DiffLine(type="add", content=content, new_line_no=curr_new)
                )
                curr_new += 1
            elif line.startswith("-"):
                lines_removed += 1
                current_hunk.lines.append(
                    DiffLine(type="del", content=content, old_line_no=curr_old)
                )
                curr_old += 1
            elif line.startswith(" "):
                current_hunk.lines.append(
                    DiffLine(
                        type="ctx", content=content, old_line_no=curr_old, new_line_no=curr_new
                    )
                )
                curr_old += 1
                curr_new += 1

        return DiffResult(
            base_id=base_id,
            target_id=target_id,
            is_target_live=is_target_live,
            lines_added=lines_added,
            lines_removed=lines_removed,
            total_changes=lines_added + lines_removed,
            hunks=hunks,
            raw_unified=raw_unified,
        )
