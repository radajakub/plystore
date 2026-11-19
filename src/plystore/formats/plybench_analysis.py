from __future__ import annotations

from plystore.formats.common import cols
from plystore.mapping import BOOL, INT, JSON, STR, STRS, Child, Column, Flatten, Format

# What plybench_analysis writes under analysis/<experiment>/reasoning/ (judge/store.py, judge/runner.py, reasoning/codebook.py,
# scripts/decide_traces.py). Annotations and verdicts name a move by its uid, a hash of (experiment, game, player, opponent, round, seq).

REASONING = r"analysis/[^/]+/reasoning"
TOKENS = cols(INT, "input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")

ANNOTATIONS = Format(
    "annotations",
    rf"{REASONING}/annotations\.jsonl",
    (
        *cols(STR, "move_uid", "annotator", "codebook_version", "notes"),
        Child("labels", "annotation_labels", (*cols(STR, "code_id", "evidence"), Column("self_corrected", BOOL), Column("description", STR))),
    ),
    log=True,
)

CONSISTENCY = Format(
    "consistency_verdicts",
    rf"{REASONING}/consistency\.jsonl",
    cols(STR, "move_uid", "annotator", "verdict", "executed_move", "concluded_move", "evidence"),
    log=True,
)

DECISIONS = Format(
    "decisions",
    rf"{REASONING}/decisions\.jsonl",
    (
        *cols(STR, "move_uid", "model", "model_string", "questions", "codebook_version"),
        Column("answers", JSON),
        Flatten("tokens", "", TOKENS),
        Column("created", STR),
    ),
    log=True,
)

# a judge's parsed answer, named by the hash of its request (the response cache)
RESPONSE = Format("judge_responses", rf"{REASONING}/responses/[^/]+\.json", (*cols(STR, "annotator", "output_text"), Flatten("tokens", "", TOKENS), Column("created", STR)))

FAILURE = Format(
    "judge_failures",
    rf"{REASONING}/responses/failed/[^/]+\.json",
    (*cols(STR, "annotator", "kind", "message", "traceback", "output_text"), Column("reasoning", STRS), Flatten("tokens", "", TOKENS), Column("created", STR)),
)

CODE = (
    Column("id", STR, "code_id"),
    *cols(STR, "name", "definition", "scope"),
    Column("parent_id", STR, "parent_code"),
    Column("examples", STRS),
    Column("inducer", STR),
    Column("etalon", JSON),
    Column("merged_into", STR),
    Column("retired", BOOL),
)

# a codebook file; every save is kept (today only the latest is on disk)
CODEBOOK = Format(
    "codebooks",
    rf"{REASONING}/codebook(\.[^/]+)?\.json",
    (
        *cols(STR, "experiment", "label", "version"),
        *cols(STRS, "induced", "induced_games"),
        Column("discovery_design", JSON),
        Child("codes", "codebook_codes", CODE),
        # op-specific fields (into, name, definition, scope, parent, source, source_version) stay in each edit's extra
        Child("edits", "codebook_edits", cols(STR, "op", "code", "reason", "at", "version")),
    ),
    history=True,
)

FORMATS = (ANNOTATIONS, CONSISTENCY, DECISIONS, RESPONSE, FAILURE, CODEBOOK)
