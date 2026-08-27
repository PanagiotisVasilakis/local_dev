CREATE TABLE lexical_index_state (
    repository_id TEXT PRIMARY KEY,
    snapshot_fingerprint TEXT NOT NULL
        CHECK (length(snapshot_fingerprint) = 64),
    policy_fingerprint TEXT NOT NULL
        CHECK (length(policy_fingerprint) = 64),
    fts_digest TEXT NOT NULL
        CHECK (length(fts_digest) = 64),
    indexed_file_count INTEGER NOT NULL CHECK (indexed_file_count >= 0),
    skipped_file_count INTEGER NOT NULL CHECK (skipped_file_count >= 0),
    lossy_file_count INTEGER NOT NULL CHECK (lossy_file_count >= 0),
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE lexical_files (
    repository_id TEXT NOT NULL
        REFERENCES lexical_index_state(repository_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    language TEXT,
    content_kind TEXT NOT NULL CHECK (content_kind IN ('text', 'empty')),
    status TEXT NOT NULL CHECK (status IN ('indexed', 'skipped_size')),
    decode_lossy INTEGER NOT NULL CHECK (decode_lossy IN (0, 1)),
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
    PRIMARY KEY (repository_id, path),
    CHECK (
        (status = 'indexed' AND chunk_count > 0)
        OR
        (status = 'skipped_size' AND chunk_count = 0 AND decode_lossy = 0)
    )
);

CREATE TRIGGER lexical_index_state_repository_id_immutable
BEFORE UPDATE OF repository_id ON lexical_index_state
BEGIN
    SELECT RAISE(ABORT, 'lexical index repository identity is immutable');
END;

CREATE TRIGGER lexical_files_no_update
BEFORE UPDATE ON lexical_files
BEGIN
    SELECT RAISE(ABORT, 'lexical files are replace-only');
END;

CREATE TABLE lexical_chunks (
    chunk_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id TEXT NOT NULL,
    path TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    chunk_id TEXT NOT NULL CHECK (length(chunk_id) = 64),
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    UNIQUE (repository_id, path, chunk_index),
    UNIQUE (repository_id, chunk_id),
    FOREIGN KEY (repository_id, path)
        REFERENCES lexical_files(repository_id, path) ON DELETE CASCADE
);

CREATE INDEX lexical_files_repository_status_idx
ON lexical_files(repository_id, status, path);

CREATE INDEX lexical_chunks_repository_path_idx
ON lexical_chunks(repository_id, path, chunk_index);

CREATE VIRTUAL TABLE lexical_chunks_fts USING fts5(
    content,
    path,
    repository_id UNINDEXED,
    content='lexical_chunks',
    content_rowid='chunk_rowid',
    tokenize='unicode61'
);

CREATE VIRTUAL TABLE lexical_chunks_fts_vocab USING fts5vocab(
    lexical_chunks_fts,
    'instance'
);

CREATE TRIGGER lexical_chunks_after_insert
AFTER INSERT ON lexical_chunks
BEGIN
    INSERT INTO lexical_chunks_fts(rowid, content, path, repository_id)
    VALUES (NEW.chunk_rowid, NEW.content, NEW.path, NEW.repository_id);
END;

CREATE TRIGGER lexical_chunks_after_delete
AFTER DELETE ON lexical_chunks
BEGIN
    INSERT INTO lexical_chunks_fts(
        lexical_chunks_fts, rowid, content, path, repository_id
    ) VALUES (
        'delete', OLD.chunk_rowid, OLD.content, OLD.path, OLD.repository_id
    );
END;

CREATE TRIGGER lexical_chunks_no_update
BEFORE UPDATE ON lexical_chunks
BEGIN
    SELECT RAISE(ABORT, 'lexical chunks are replace-only');
END;
