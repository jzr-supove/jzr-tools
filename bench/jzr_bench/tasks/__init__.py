"""The task registry."""

from __future__ import annotations

from . import batch, compress, gif, heic, hello, pdf, pdfsplit, rename, tags, videosize, xlsx, xlsxjson
from .base import CheckResult, Task

TASKS: dict[str, Task] = {
    t.name: t
    for t in (
        hello.TASK,
        gif.TASK,
        heic.TASK,
        pdf.TASK,
        compress.TASK,
        xlsx.TASK,
        videosize.TASK,
        tags.TASK,
        pdfsplit.TASK,
        batch.TASK,
        xlsxjson.TASK,
        rename.TASK,
    )
}
# Not run by default: kept out of the tuning of the skill and MCP output, run for the final comparison.
HELD_OUT = frozenset({"compress", "xlsx", "videosize", "tags", "pdfsplit", "batch", "xlsxjson", "rename"})


def get_task(name: str) -> Task:
    try:
        return TASKS[name]
    except KeyError:
        raise KeyError(f"unknown task {name!r}; known tasks: {', '.join(TASKS)}") from None


__all__ = ["HELD_OUT", "TASKS", "CheckResult", "Task", "get_task"]
