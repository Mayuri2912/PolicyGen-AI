SET LOCAL search_path TO policygen;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_analyses_workspace_id ON analyses (workspace_id);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_jobs_workspace_id ON jobs (workspace_id);
ALTER TABLE processing_errors ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_processing_errors_workspace_id ON processing_errors (workspace_id);
ALTER TABLE uploaded_files ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_uploaded_files_workspace_id ON uploaded_files (workspace_id);
ALTER TABLE analysis_findings ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_analysis_findings_workspace_id ON analysis_findings (workspace_id);
ALTER TABLE extracted_documents ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_extracted_documents_workspace_id ON extracted_documents (workspace_id);
ALTER TABLE policies ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_policies_workspace_id ON policies (workspace_id);
ALTER TABLE policy_versions ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_policy_versions_workspace_id ON policy_versions (workspace_id);
ALTER TABLE review_events ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(32) NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS ix_review_events_workspace_id ON review_events (workspace_id);

CREATE TABLE IF NOT EXISTS accounts (
	id VARCHAR(32) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	password_hash VARCHAR(200) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	workspace_id VARCHAR(32) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
)

;
CREATE INDEX IF NOT EXISTS ix_accounts_workspace_id ON accounts (workspace_id);
GRANT SELECT, INSERT, UPDATE, DELETE ON accounts TO policygen_app;
REVOKE ALL ON accounts FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS account_sessions (
	id SERIAL NOT NULL, 
	account_id VARCHAR(32) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE, 
	UNIQUE (token_hash)
)

;
GRANT SELECT, INSERT, UPDATE, DELETE ON account_sessions TO policygen_app;
REVOKE ALL ON account_sessions FROM PUBLIC, anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE account_sessions_id_seq TO policygen_app;
