from __future__ import annotations

from plystore.formats import plyagents, plybench, plybench_analysis
from plystore.mapping import Format

# Every format plystore keeps in typed tables, following the repos that write them. A record whose key no format matches (or that is not the
# shape its format keeps) is kept whole (unmapped). Every schema of the database has the tables of every format: the repo that ran the work
# decides the schema, not the format (training runs from the plyagents checkout write plybench's formats into the plyagents schema).

FORMATS: tuple[Format, ...] = (*plybench.FORMATS, *plyagents.FORMATS, *plybench_analysis.FORMATS)


def find(key: str) -> Format | None:
    return next((fmt for fmt in FORMATS if fmt.matches(key)), None)
