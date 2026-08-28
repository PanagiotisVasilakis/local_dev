CREATE TABLE structural_index_state (
    repository_id TEXT PRIMARY KEY,
    snapshot_fingerprint TEXT NOT NULL CHECK (length(snapshot_fingerprint) = 64),
    policy_fingerprint TEXT NOT NULL CHECK (length(policy_fingerprint) = 64),
    structure_digest TEXT NOT NULL CHECK (length(structure_digest) = 64),
    file_count INTEGER NOT NULL CHECK (file_count >= 0),
    symbol_count INTEGER NOT NULL CHECK (symbol_count >= 0),
    import_count INTEGER NOT NULL CHECK (import_count >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE structural_files (
    repository_id TEXT NOT NULL
        REFERENCES structural_index_state(repository_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    language TEXT,
    content_kind TEXT NOT NULL CHECK (content_kind IN ('empty', 'text', 'binary')),
    status TEXT NOT NULL CHECK (status IN (
        'indexed', 'parse_error', 'unsupported_language', 'unsupported_content', 'skipped_size'
    )),
    error_message TEXT,
    symbol_count INTEGER NOT NULL CHECK (symbol_count >= 0),
    import_count INTEGER NOT NULL CHECK (import_count >= 0),
    PRIMARY KEY (repository_id, path),
    CHECK ((status = 'parse_error') = (error_message IS NOT NULL)),
    CHECK (error_message IS NULL OR length(error_message) > 0),
    CHECK (status = 'indexed' OR (symbol_count = 0 AND import_count = 0))
);

CREATE TABLE structural_symbols (
    repository_id TEXT NOT NULL,
    path TEXT NOT NULL,
    symbol_id TEXT NOT NULL CHECK (length(symbol_id) = 64),
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    kind TEXT NOT NULL CHECK (kind IN (
        'class', 'function', 'async_function', 'method', 'async_method', 'variable', 'type_alias'
    )),
    name TEXT NOT NULL CHECK (length(name) > 0),
    qualified_name TEXT NOT NULL CHECK (length(qualified_name) > 0),
    parent_symbol_id TEXT,
    parent_qualified_name TEXT,
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    start_col INTEGER NOT NULL CHECK (start_col >= 0),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    end_col INTEGER NOT NULL CHECK (end_col >= 0),
    signature TEXT,
    decorators_json TEXT NOT NULL,
    UNIQUE (repository_id, symbol_id),
    UNIQUE (repository_id, path, symbol_id),
    FOREIGN KEY (repository_id, path)
        REFERENCES structural_files(repository_id, path) ON DELETE CASCADE,
    FOREIGN KEY (repository_id, path, parent_symbol_id)
        REFERENCES structural_symbols(repository_id, path, symbol_id),
    CHECK ((parent_symbol_id IS NULL) = (parent_qualified_name IS NULL)),
    CHECK (end_line > start_line OR end_col >= start_col)
);

CREATE TABLE structural_imports (
    repository_id TEXT NOT NULL,
    path TEXT NOT NULL,
    import_id TEXT NOT NULL CHECK (length(import_id) = 64),
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    kind TEXT NOT NULL CHECK (kind IN ('import', 'from_import')),
    module TEXT,
    name TEXT NOT NULL CHECK (length(name) > 0),
    alias TEXT,
    level INTEGER NOT NULL CHECK (level >= 0),
    scope_symbol_id TEXT,
    scope_qualified_name TEXT,
    line INTEGER NOT NULL CHECK (line >= 1),
    col INTEGER NOT NULL CHECK (col >= 0),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    UNIQUE (repository_id, import_id),
    FOREIGN KEY (repository_id, path)
        REFERENCES structural_files(repository_id, path) ON DELETE CASCADE,
    FOREIGN KEY (repository_id, path, scope_symbol_id)
        REFERENCES structural_symbols(repository_id, path, symbol_id),
    CHECK ((scope_symbol_id IS NULL) = (scope_qualified_name IS NULL)),
    CHECK (
        (kind = 'import'
            AND level = 0
            AND module IS NOT NULL
            AND module = name)
        OR
        (kind = 'from_import' AND (module IS NOT NULL OR level > 0))
    )
);

CREATE INDEX structural_symbols_lookup_idx
ON structural_symbols(repository_id, name, qualified_name, path, start_line, start_col);

CREATE INDEX structural_imports_lookup_idx
ON structural_imports(repository_id, module, name, path, line, col);

CREATE TRIGGER structural_state_repository_immutable
BEFORE UPDATE OF repository_id ON structural_index_state
BEGIN
    SELECT RAISE(ABORT, 'structural repository identity is immutable');
END;

CREATE TRIGGER structural_files_no_update
BEFORE UPDATE ON structural_files
BEGIN
    SELECT RAISE(ABORT, 'structural files are replace-only');
END;

CREATE TRIGGER structural_symbols_no_update
BEFORE UPDATE ON structural_symbols
BEGIN
    SELECT RAISE(ABORT, 'structural symbols are immutable');
END;

CREATE TRIGGER structural_imports_no_update
BEFORE UPDATE ON structural_imports
BEGIN
    SELECT RAISE(ABORT, 'structural imports are immutable');
END;

CREATE TRIGGER structural_symbol_coherence
BEFORE INSERT ON structural_symbols
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM structural_files AS f
        WHERE f.repository_id = NEW.repository_id
          AND f.path = NEW.path
          AND f.status = 'indexed'
          AND f.file_sha256 = NEW.file_sha256
    ) THEN RAISE(ABORT, 'structural symbol file coherence violation') END;
    SELECT CASE WHEN NEW.parent_symbol_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM structural_symbols AS p
        WHERE p.repository_id = NEW.repository_id
          AND p.path = NEW.path
          AND p.symbol_id = NEW.parent_symbol_id
          AND p.qualified_name = NEW.parent_qualified_name
          AND p.kind IN ('class', 'function', 'async_function', 'method', 'async_method')
    ) THEN RAISE(ABORT, 'structural parent coherence violation') END;
END;

CREATE TRIGGER structural_import_coherence
BEFORE INSERT ON structural_imports
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM structural_files AS f
        WHERE f.repository_id = NEW.repository_id
          AND f.path = NEW.path
          AND f.status = 'indexed'
          AND f.file_sha256 = NEW.file_sha256
    ) THEN RAISE(ABORT, 'structural import file coherence violation') END;
    SELECT CASE WHEN NEW.scope_symbol_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM structural_symbols AS s
        WHERE s.repository_id = NEW.repository_id
          AND s.path = NEW.path
          AND s.symbol_id = NEW.scope_symbol_id
          AND s.qualified_name = NEW.scope_qualified_name
          AND s.kind IN ('class', 'function', 'async_function', 'method', 'async_method')
    ) THEN RAISE(ABORT, 'structural import scope coherence violation') END;
END;
