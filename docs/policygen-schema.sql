SET search_path = policygen;

CREATE TABLE analyses (
	id VARCHAR(32) NOT NULL,
	job_id VARCHAR(32),
	status VARCHAR(32) NOT NULL,
	source_files JSON NOT NULL,
	provider VARCHAR(32) NOT NULL,
	model VARCHAR(64) NOT NULL,
	simulated BOOLEAN NOT NULL,
	summary JSON NOT NULL,
	error_message TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE TABLE jobs (
	id VARCHAR(32) NOT NULL,
	type VARCHAR(32) NOT NULL,
	status VARCHAR(16) NOT NULL,
	stage VARCHAR(32) NOT NULL,
	progress INTEGER NOT NULL,
	message TEXT NOT NULL,
	error_message TEXT,
	payload JSON NOT NULL,
	result JSON NOT NULL,
	dedupe_key VARCHAR(64),
	cancel_requested BOOLEAN NOT NULL,
	attempts INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id)
)

;

CREATE TABLE processing_errors (
	id SERIAL NOT NULL,
	job_id VARCHAR(32),
	subject_type VARCHAR(24) NOT NULL,
	subject_id VARCHAR(32),
	stage VARCHAR(32) NOT NULL,
	code VARCHAR(64) NOT NULL,
	message TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE TABLE uploaded_files (
	id VARCHAR(32) NOT NULL,
	original_name VARCHAR(255) NOT NULL,
	extension VARCHAR(16) NOT NULL,
	declared_mime VARCHAR(128) NOT NULL,
	size_bytes INTEGER NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	storage_name VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	error_code VARCHAR(64),
	error_message TEXT,
	warnings JSON NOT NULL,
	source_deleted BOOLEAN NOT NULL,
	extraction_job_id VARCHAR(32),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE TABLE analysis_findings (
	id SERIAL NOT NULL,
	analysis_id VARCHAR(32) NOT NULL,
	kind VARCHAR(24) NOT NULL,
	ref_id VARCHAR(24) NOT NULL,
	position INTEGER NOT NULL,
	data JSON NOT NULL,
	original_data JSON NOT NULL,
	user_edited BOOLEAN NOT NULL,
	excluded BOOLEAN NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
)

;

CREATE TABLE extracted_documents (
	id VARCHAR(32) NOT NULL,
	file_id VARCHAR(32) NOT NULL,
	title VARCHAR(500) NOT NULL,
	file_type VARCHAR(16) NOT NULL,
	char_count INTEGER NOT NULL,
	section_count INTEGER NOT NULL,
	table_count INTEGER NOT NULL,
	doc_metadata JSON NOT NULL,
	content_name VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (file_id),
	FOREIGN KEY(file_id) REFERENCES uploaded_files (id) ON DELETE CASCADE
)

;

CREATE TABLE policies (
	id VARCHAR(32) NOT NULL,
	analysis_id VARCHAR(32) NOT NULL,
	job_id VARCHAR(32),
	policy_type VARCHAR(64) NOT NULL,
	title VARCHAR(300) NOT NULL,
	config JSON NOT NULL,
	status VARCHAR(32) NOT NULL,
	review_status VARCHAR(24) NOT NULL,
	approved_version INTEGER,
	current_version INTEGER NOT NULL,
	provider VARCHAR(32) NOT NULL,
	model VARCHAR(64) NOT NULL,
	simulated BOOLEAN NOT NULL,
	source_files JSON NOT NULL,
	error_message TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
)

;

CREATE TABLE policy_versions (
	id SERIAL NOT NULL,
	policy_id VARCHAR(32) NOT NULL,
	version_number INTEGER NOT NULL,
	content JSON NOT NULL,
	quality JSON NOT NULL,
	origin VARCHAR(24) NOT NULL,
	change_note TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(policy_id) REFERENCES policies (id) ON DELETE CASCADE
)

;

CREATE TABLE review_events (
	id SERIAL NOT NULL,
	policy_id VARCHAR(32) NOT NULL,
	version_number INTEGER NOT NULL,
	action VARCHAR(24) NOT NULL,
	actor VARCHAR(120) NOT NULL,
	comment TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(policy_id) REFERENCES policies (id) ON DELETE CASCADE
)

;
CREATE INDEX ix_analyses_status ON analyses (status);
CREATE INDEX ix_jobs_type ON jobs (type);
CREATE INDEX ix_jobs_status ON jobs (status);
CREATE INDEX ix_jobs_dedupe_key ON jobs (dedupe_key);
CREATE INDEX ix_processing_errors_job_id ON processing_errors (job_id);
CREATE INDEX ix_uploaded_files_sha256 ON uploaded_files (sha256);
CREATE INDEX ix_uploaded_files_status ON uploaded_files (status);
CREATE INDEX ix_analysis_findings_analysis_id ON analysis_findings (analysis_id);
CREATE INDEX ix_analysis_findings_kind ON analysis_findings (kind);
CREATE INDEX ix_policies_analysis_id ON policies (analysis_id);
CREATE INDEX ix_policies_status ON policies (status);
CREATE INDEX ix_policy_versions_policy_id ON policy_versions (policy_id);
CREATE INDEX ix_review_events_policy_id ON review_events (policy_id);
ALTER TABLE policygen.analyses ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.analyses TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.jobs ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.jobs TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.processing_errors ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.processing_errors TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.uploaded_files ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.uploaded_files TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.analysis_findings ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.analysis_findings TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.extracted_documents ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.extracted_documents TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.policies ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.policies TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.policy_versions ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.policy_versions TO policygen_app USING (true) WITH CHECK (true);
ALTER TABLE policygen.review_events ENABLE ROW LEVEL SECURITY; CREATE POLICY backend_access ON policygen.review_events TO policygen_app USING (true) WITH CHECK (true);
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA policygen TO policygen_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA policygen TO policygen_app;
