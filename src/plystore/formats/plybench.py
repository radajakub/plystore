from __future__ import annotations

from plystore.formats import plyagents
from plystore.formats.common import cols
from plystore.mapping import BOOL, FLOAT, INT, INTS, JSON, SHARED, STR, STRS, Child, Column, Flatten, Format

# What plybench writes (trackers/result_tracker.py, game_tracker.py, harness/training/training.py, player/learnable.py, prompt_block.py) and
# the experiment files it reads. A benchmark matchup and a phase of a training epoch are both a metadata.json with its game_N.json beside it;
# records link by folder (a game's matchup is the metadata.json in its folder), since a game is written before its matchup's first metadata.

LLM_CALL = (
    *cols(STR, "role", "provider", "model", "model_string"),
    Column("system_message", SHARED),
    *cols(STR, "prompt_message", "reasoning_trace", "full_output"),
    *cols(INT, "input_tokens", "output_tokens", "reasoning_tokens", "seed"),
    Column("failure", STR),
    Column("options", JSON),
    Column("finish_reason", STR),
    Column("truncated", BOOL),
    Column("retries", INT),
    Column("batch", BOOL),
    Column("about", JSON),
)

# the `data` of a step: an LLM player's texts (trackers/step_data.py), then what plyagents' textrl player adds
STEP_DATA = (*cols(STR, "reasoning_trace", "full_output"), Column("system_message", SHARED), Column("prompt_message", STR), *plyagents.STEP_DATA)

STEP = (
    Column("seq", INT),
    *cols(STR, "player_name", "player_hash", "serialized_state", "observation", "move"),
    *cols(INT, "input_tokens", "output_tokens", "reasoning_tokens", "llm_seed"),
    Column("model_string", STR),
    Column("model_options", JSON),
    Column("finish_reason", STR),
    Column("truncated", BOOL),
    Column("retries", INT),
    Column("extracted", STR),
    Column("blocks", STRS),
    Child("calls", "step_calls", LLM_CALL),
    Flatten("data", "data_", STEP_DATA),
)

# what a learner records at the end of a game (player/learnable.py; `outcome` from plyagents): paths of the states it started from and learned
GAME_PLAYER = (Flatten("state", "state_", cols(STR, "checkpoint", "before")), Column("learned", STR), Column("outcome", FLOAT))

GAME = Format(
    "games",
    r"results/.+/game_\d+\.json",
    (
        Column("game_round", INT),
        *cols(STR, "i_player", "o_player"),
        *cols(JSON, "instance_params", "other_params"),
        Column("seq", INT),
        Column("seed", INT),
        Child("steps", "game_steps", STEP),
        Flatten(
            "ending",
            "end_",
            (
                Column("seq", INT),
                Column("observation", STR),
                Column("result", INT),
                Column("serialized_state", STR),
                Child("players", "game_players", GAME_PLAYER, by=("player_hash",)),
            ),
        ),
    ),
)


def _sessions(table: str) -> Child:
    # the provenance of each session that touched the record (common/provenance.py)
    commit = (Column("commit", STR), Column("dirty", BOOL))
    return Child(
        "sessions",
        table,
        (
            Column("started", STR),
            Child("packages", f"{table}_packages", (Column("version", STR), Flatten("source", "source_", commit)), by=("name",)),
            Flatten("working_directory", "workdir_", commit),
            Column("command", STRS),
            Column("python", STR),
            # each player's key, config and every expanded parameter: open-ended
            Column("players", JSON),
            Column("rounds", INTS),
        ),
    )


MATCHUP = Format(
    "matchups",
    r"results/.+/metadata\.json",
    (
        # a benchmark's name, or a training phase's whole scope path (place.experiment is then the name)
        Column("experiment", STR),
        *cols(STR, "i_config", "o_config", "game_config"),
        Column("n_games", INT),
        Column("completed", INTS),
        Column("seed", INT),
        _sessions("matchup_sessions"),
        Child("summary", "matchup_metrics", (Column("n", INT), *cols(FLOAT, "sum", "sumsq", "value", "lower", "upper")), by=("metric",)),
        Flatten(
            "place",
            "place_",
            (
                Column("experiment", STR),
                Flatten("run", "run_", (*cols(STR, "game_config", "trainee_config", "trainer_config", "training_config"), Column("replicate", INT))),
                *cols(INT, "epoch", "epochs"),
                Column("phase", STR),
            ),
        ),
    ),
)

TRAINING_RUN = Format(
    "training_runs",
    r"results/training/.+/run\.json",
    (
        *cols(STR, "game_config", "trainee_config", "trainer_config", "training_config"),
        Column("replicate", INT),
        Column("testers", STRS),
        Column("seed", INT),
        _sessions("run_sessions"),
    ),
)

# the calls a learner made for one update (a JSON list beside update.json)
LEARNER_CALLS = Format("learner_calls", r"results/training/.+/learner/[^/]+/llm_calls\.json", LLM_CALL, items="learner_call_items")

LLM_BLOCKS = Format(
    "llm_blocks",
    r"results/llm_blocks\.jsonl",
    (
        *cols(STR, "time", "player", "reason"),
        *cols(INT, "attempt", "max_attempts", "input_tokens"),
        Column("system_message", SHARED),
        Column("prompt_message", STR),
        Column("id", STR, "block_id"),
    ),
    log=True,
)

TOGGLE = (Column("value", STR), Column("enabled", BOOL), Column("label", STR))
AXES = ("game_configs", "player_configs", "opponents", "games", "players", "trainers", "testers", "training_configs")

# an experiment file (configs/benchmark_config.py, training_harness_config.py); every saved version is kept
EXPERIMENT = Format(
    "experiment_configs",
    r"experiments/(benchmarks|training)/[^/]+\.json",
    (
        *(Child(axis, "experiment_items", TOGGLE, part=axis) for axis in AXES),
        *cols(INT, "num_games", "num_replicates", "seed"),
        *cols(STR, "colour_rule", "baseline"),
    ),
    history=True,
)

FORMATS = (GAME, MATCHUP, TRAINING_RUN, LEARNER_CALLS, LLM_BLOCKS, EXPERIMENT)
