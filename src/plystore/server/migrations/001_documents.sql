-- Documents by key (JSON in data, text in text), the earlier versions of replaced documents, and append-only logs. Keys sort and compare by
-- bytes (COLLATE "C"), so a prefix is a range of the primary key and the order is the one the other backends give.
CREATE TABLE documents (
    key text COLLATE "C" PRIMARY KEY,
    data jsonb,
    text text,
    created timestamptz NOT NULL DEFAULT now(),
    CHECK ((data IS NULL) <> (text IS NULL))
);

CREATE TABLE history (
    key text COLLATE "C" NOT NULL,
    data jsonb,
    text text,
    created timestamptz NOT NULL,
    replaced timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX history_key ON history (key);

CREATE TABLE logs (
    id bigserial PRIMARY KEY,
    key text COLLATE "C" NOT NULL,
    record jsonb NOT NULL,
    created timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX logs_key ON logs (key, id);
