CREATE TABLE personas (
    id TEXT PRIMARY KEY NOT NULL,
    display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 80),
    relationship TEXT NOT NULL,
    target_period TEXT NOT NULL,
    address_terms TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','reviewing','ready','deleting')),
    active_version_id TEXT,
    memory_enabled INTEGER NOT NULL DEFAULT 1 CHECK(memory_enabled IN (0,1)),
    development_enabled INTEGER NOT NULL DEFAULT 0 CHECK(development_enabled IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_at TEXT NOT NULL,
    CHECK(development_enabled <= memory_enabled),
    FOREIGN KEY (id, active_version_id) REFERENCES profile_versions(persona_id,id)
);

CREATE TABLE sources (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('chat_image','chat_text','recollection','hypothetical','style_preference')),
    original_path TEXT,
    original_text TEXT,
    content_hash TEXT NOT NULL,
    context_period TEXT NOT NULL,
    target_speaker TEXT,
    status TEXT NOT NULL CHECK(status IN ('draft','confirmed','withdrawn')),
    created_at TEXT NOT NULL,
    CHECK((kind = 'chat_image' AND original_path IS NOT NULL AND original_text IS NULL)
        OR (kind <> 'chat_image' AND original_path IS NULL AND original_text IS NOT NULL)),
    CHECK(status <> 'confirmed' OR target_speaker IS NOT NULL),
    PRIMARY KEY (persona_id,id)
);

CREATE TABLE evidence (
    persona_id TEXT NOT NULL,
    id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    locator TEXT NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT NOT NULL,
    context TEXT NOT NULL,
    occurred_at TEXT,
    event_key TEXT,
    verification TEXT NOT NULL CHECK(verification IN ('pending','user_confirmed')),
    PRIMARY KEY (persona_id,id),
    UNIQUE (persona_id,source_id,locator),
    FOREIGN KEY (persona_id,source_id) REFERENCES sources(persona_id,id) ON DELETE CASCADE
);

CREATE TABLE claims (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('style','situational','relational','trait','fact')),
    statement TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK(evidence_level IN ('supported','tentative','unknown')),
    origin TEXT NOT NULL CHECK(origin IN ('inferred','recalled','user_defined')),
    scope TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK(review_status IN ('proposed','accepted','rejected','superseded','needs_review')),
    supersedes_id TEXT,
    PRIMARY KEY (persona_id,id),
    FOREIGN KEY (persona_id,supersedes_id) REFERENCES claims(persona_id,id)
);

CREATE TABLE claim_evidence (
    persona_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK(relation IN ('support','contradict')),
    PRIMARY KEY (persona_id,claim_id,evidence_id),
    FOREIGN KEY (persona_id,claim_id) REFERENCES claims(persona_id,id) ON DELETE CASCADE,
    FOREIGN KEY (persona_id,evidence_id) REFERENCES evidence(persona_id,id) ON DELETE CASCADE
);
CREATE INDEX claim_evidence_by_evidence ON claim_evidence(persona_id,evidence_id);

CREATE TABLE profile_versions (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK(version_no >= 1),
    snapshot_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (persona_id,id),
    UNIQUE (persona_id,version_no)
);

CREATE TABLE profile_proposals (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    reviewed_version INTEGER NOT NULL,
    claim_ids TEXT NOT NULL,
    unknowns TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','stale')),
    PRIMARY KEY (persona_id,id)
);

CREATE TABLE questions (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    target_claim_ids TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK(purpose IN ('missing','conflict','context','counterexample')),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','answered','skipped')),
    answer_source_id TEXT,
    PRIMARY KEY (persona_id,id),
    FOREIGN KEY (persona_id,answer_source_id) REFERENCES sources(persona_id,id)
);

CREATE TABLE sessions (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    profile_version_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','closed')),
    PRIMARY KEY (persona_id,id),
    FOREIGN KEY (persona_id,profile_version_id) REFERENCES profile_versions(persona_id,id)
);

CREATE TABLE memories (
    persona_id TEXT NOT NULL,
    id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('user_update','shared_exchange','agreement')),
    source_turn_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (persona_id,id),
    UNIQUE (persona_id,session_id,source_turn_id),
    FOREIGN KEY (persona_id,session_id) REFERENCES sessions(persona_id,id) ON DELETE CASCADE
);

CREATE TABLE development_proposals (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    base_version_id TEXT NOT NULL,
    patch_json TEXT NOT NULL,
    memory_ids TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','rejected','stale')),
    PRIMARY KEY (persona_id,id),
    FOREIGN KEY (persona_id,base_version_id) REFERENCES profile_versions(persona_id,id)
);

CREATE TABLE operations (
    request_id TEXT PRIMARY KEY NOT NULL,
    operation TEXT NOT NULL,
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    payload_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX operations_by_persona ON operations(persona_id);

CREATE TABLE audit (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    event TEXT NOT NULL,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (persona_id,id)
);

CREATE TABLE deletion_confirmations (
    persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    revision INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (persona_id,token_hash)
);

CREATE TABLE pending_deletions (
    persona_id TEXT PRIMARY KEY NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    request_id TEXT UNIQUE NOT NULL,
    payload_hash TEXT NOT NULL
);

CREATE TABLE deletion_tombstones (
    request_id TEXT PRIMARY KEY NOT NULL,
    payload_hash TEXT
);
