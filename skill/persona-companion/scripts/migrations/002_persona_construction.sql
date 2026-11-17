ALTER TABLE profile_proposals ADD COLUMN correction_json TEXT;
ALTER TABLE questions ADD COLUMN question_key TEXT;
ALTER TABLE questions ADD COLUMN skipped_revision INTEGER;

CREATE INDEX questions_by_key ON questions(persona_id, question_key, status);
