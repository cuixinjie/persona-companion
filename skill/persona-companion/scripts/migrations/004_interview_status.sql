ALTER TABLE personas ADD COLUMN interview_status TEXT NOT NULL DEFAULT 'open'
    CHECK(interview_status IN ('open', 'closed'));

-- 升级前已准备的回复可能带有旧版错期结论，必须重新准备。
UPDATE sessions SET turn_key = randomblob(32) WHERE status = 'active';
