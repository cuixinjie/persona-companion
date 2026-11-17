ALTER TABLE sessions ADD COLUMN turn_key BLOB;
ALTER TABLE development_proposals ADD COLUMN reviewed_version INTEGER;
ALTER TABLE development_proposals ADD COLUMN fingerprint TEXT;

CREATE INDEX memories_for_context ON memories(persona_id, status, created_at DESC, id DESC);
CREATE INDEX development_for_review ON development_proposals(persona_id, status, reviewed_version);
