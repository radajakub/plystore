-- Typed columns of every game record (plybench's GameTracker.to_dict, a document named game_N.json), filled by a trigger on each write, so
-- queries over many games (win rates, lengths per matchup) read columns, not documents. The document stays the record: a row here is a copy,
-- removed with its document. A value of an unexpected type or size gives NULL in its column; indexing never refuses a write.
CREATE TABLE games (
    key text COLLATE "C" PRIMARY KEY REFERENCES documents (key) ON DELETE CASCADE,
    folder text COLLATE "C" NOT NULL,
    round integer,
    i_player text,
    o_player text,
    result integer,
    moves integer,
    seed numeric
);
CREATE INDEX games_folder ON games (folder, round);

CREATE FUNCTION game_integer(value jsonb) RETURNS integer LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN jsonb_typeof(value) = 'number' AND abs(value::numeric) < 2147483647 THEN round(value::numeric)::integer END
$$;

CREATE FUNCTION index_game(document_key text, data jsonb) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM games WHERE key = document_key;
    IF data IS NOT NULL AND document_key ~ '(^|/)game_[0-9]+\.json$' AND jsonb_typeof(data) = 'object'
       AND jsonb_typeof(data -> 'steps') = 'array' AND data ? 'game_round' THEN
        INSERT INTO games (key, folder, round, i_player, o_player, result, moves, seed)
        VALUES (
            document_key,
            regexp_replace(document_key, '/[^/]*$', ''),
            game_integer(data -> 'game_round'),
            data ->> 'i_player',
            data ->> 'o_player',
            game_integer(data -> 'ending' -> 'result'),
            jsonb_array_length(data -> 'steps'),
            CASE WHEN jsonb_typeof(data -> 'seed') = 'number' THEN (data -> 'seed')::numeric END
        );
    END IF;
END
$$;

CREATE FUNCTION index_game_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM index_game(NEW.key, NEW.data);
    RETURN NEW;
END
$$;

CREATE TRIGGER documents_games AFTER INSERT OR UPDATE ON documents FOR EACH ROW EXECUTE FUNCTION index_game_trigger();

-- the games already there
SELECT index_game(key, data) FROM documents WHERE key ~ '(^|/)game_[0-9]+\.json$';
