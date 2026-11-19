from __future__ import annotations

from plystore.formats.common import cols
from plystore.mapping import BOOL, FLOAT, INT, INTS, JSON, SHARED, STR, STRS, Child, Choice, Column, Flatten, Format

# What plyagents writes inside plybench's places (textrl/player.py, step.py): the data of a textrl step, the learner state of a checkpoint or
# after a commit (state.json), and the update of one game, batch or epoch (update.json). Which shape a block has (a memory of texts, a table of
# values) depends on the configured actor and critic: a Choice per block.

# the `data` of a textrl step, beside plybench's own data fields (formats/plybench.py)
STEP_DATA = (
    Column("key", STR),
    Column("action", STR),
    Column("player", INT),
    Column("os_state", STR),
    Column("legal", STRS),
    Column("legal_numbers", INTS),
    Column("context", STR),
    Column("rules", SHARED),
    Column("reasoning", STR),
    Column("guidance", STR),
    # by actor: what was read from the memory, how the move was selected, the table's estimates; open-ended
    *cols(JSON, "read", "select", "estimates", "guidance_parts", "counts"),
)

MEMORY_ENTRY = (Column("text", SHARED), Column("visits", INT), Column("actions", JSON), Column("writes", INT), Column("samples", INT))
VALUE = (Column("value", FLOAT), Column("count", INT))
REFERENCE_COUNT = (Flatten("results", "", cols(INT, "win", "loss", "draw")), Column("sum", FLOAT), Column("sum_sq", FLOAT))


def _memory(block: str) -> Child:
    return Child(block, "memory_entries", MEMORY_ENTRY, by=("entry_key",), part=block)


LEARNER_STATE = Format(
    "learner_states",
    r"results/training/.+/(checkpoint|learner/[^/]+/state)/state\.json",
    (
        Choice("actor", (_memory("actor"), Child("actor", "table_entries", VALUE, by=("entry_key", "action"), part="actor"))),
        Choice(
            "critic",
            (
                _memory("critic"),
                Flatten(
                    "critic",
                    "critic_",
                    (
                        Child("values", "value_entries", VALUE, by=("entry_key",), part="critic"),
                        Child("action_values", "table_entries", VALUE, by=("entry_key", "action"), part="critic"),
                    ),
                ),
            ),
        ),
        Child("reference", "reference_counts", REFERENCE_COUNT, by=("entry_key", "move")),
    ),
)

VERDICT = (*cols(STR, "grade", "reason", "lesson", "critic_note", "error"), Column("estimate", FLOAT))
WRITE = (
    Column("key", STR, "entry_key"),
    *cols(STR, "old", "lesson"),
    Column("alpha", FLOAT),
    *cols(STR, "compressed", "new"),
    Column("cut", BOOL),
    Column("steps", INTS),
    *cols(INT, "writes_before", "budget", "keep", "call"),
    *cols(BOOL, "cut_old", "cut_lesson"),
    Column("parts", STRS),
    Column("changed", FLOAT),
    Column("reduce_call", INT),
)
SAMPLE = (Column("key", STR, "entry_key"), Column("action", STR), Column("target", FLOAT), Column("source", STR))
REFERENCE_STEP = (
    Column("step", INT),
    Column("key", STR, "entry_key"),
    Column("move", STR),
    Column("legal", STRS),
    Column("counts", JSON),
    Flatten("critic", "critic_", (Column("value", FLOAT), Column("action_values", JSON))),
    Column("solved", JSON),
    Column("grade", STR),
)


def _update_block(block: str) -> Choice:
    # a memory's update (visits, verdicts, writes) or a table's (samples)
    memory = (
        Column("visits", JSON),
        Child("feedback", "update_verdicts", VERDICT, part=block),
        Child("writes", "update_writes", WRITE, part=block),
        *cols(JSON, "gradients", "limits"),
    )
    return Choice(block, (Flatten(block, f"{block}_", memory), Flatten(block, f"{block}_", (Child("samples", "update_samples", SAMPLE, part=block),))))


LEARNER_UPDATE = Format(
    "learner_updates",
    r"results/training/.+/learner/[^/]+/update\.json",
    (
        _update_block("actor"),
        _update_block("critic"),
        Flatten("reference", "ref_", (Column("outcome", FLOAT), Column("result", STR), Child("steps", "update_reference_steps", REFERENCE_STEP))),
        # the assessments, verdicts and loss inputs of the update; their keys depend on the losses and on batching
        Column("record", JSON),
    ),
)

FORMATS = (LEARNER_STATE, LEARNER_UPDATE)
