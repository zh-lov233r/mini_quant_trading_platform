-- Additive Signal Center schema. Apply only after target preflight and authorization.
BEGIN;
CREATE TABLE IF NOT EXISTS signal_data_ready (
	market VARCHAR(2) NOT NULL,
	session_date DATE NOT NULL,
	version VARCHAR(64) NOT NULL,
	coverage JSONB NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE NOT NULL,
	valid BOOLEAN NOT NULL,
	PRIMARY KEY (market, session_date)
);

CREATE TABLE IF NOT EXISTS signal_market_sessions (
	market VARCHAR(2) NOT NULL,
	session_date DATE NOT NULL,
	is_open BOOLEAN NOT NULL,
	opens_at TIMESTAMP WITH TIME ZONE,
	closes_at TIMESTAMP WITH TIME ZONE,
	source VARCHAR(64) NOT NULL,
	fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (market, session_date)
);

CREATE TABLE IF NOT EXISTS signal_scan_plans (
	id UUID NOT NULL,
	name VARCHAR(128) NOT NULL,
	market VARCHAR(2) NOT NULL,
	basket_id UUID NOT NULL,
	strategies JSONB NOT NULL,
	language VARCHAR(5) NOT NULL,
	recipients JSONB NOT NULL,
	retention_sessions INTEGER NOT NULL,
	enabled BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CHECK (market IN ('US','CN')),
	CHECK (retention_sessions > 0)
);

CREATE TABLE IF NOT EXISTS signal_scan_runs (
	id UUID NOT NULL,
	plan_id UUID,
	request_key VARCHAR(128) NOT NULL,
	name VARCHAR(128) NOT NULL,
	market VARCHAR(2) NOT NULL,
	session_date DATE NOT NULL,
	manifest JSONB NOT NULL,
	coverage JSONB NOT NULL,
	assets JSONB NOT NULL,
	purged_at TIMESTAMP WITH TIME ZONE,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	claimed_by VARCHAR(128),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_signal_schedule_session UNIQUE (plan_id, session_date),
	UNIQUE (request_key)
);

CREATE INDEX IF NOT EXISTS ix_signal_scan_runs_session_date ON signal_scan_runs (session_date);

CREATE INDEX IF NOT EXISTS ix_signal_scan_runs_status ON signal_scan_runs (status);

CREATE TABLE IF NOT EXISTS signal_reports (
	id UUID NOT NULL,
	scan_id UUID NOT NULL,
	request_key VARCHAR(128) NOT NULL,
	language VARCHAR(5) NOT NULL,
	automatic BOOLEAN NOT NULL,
	document JSONB,
	assets JSONB NOT NULL,
	render_errors JSONB NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	expires_at TIMESTAMP WITH TIME ZONE,
	retention_sessions INTEGER,
	retention_reason VARCHAR(128),
	expired_at TIMESTAMP WITH TIME ZONE,
	summary JSONB NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	claimed_by VARCHAR(128),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(scan_id) REFERENCES signal_scan_runs (id),
	UNIQUE (request_key)
);

CREATE INDEX IF NOT EXISTS ix_signal_reports_scan_id ON signal_reports (scan_id);

CREATE INDEX IF NOT EXISTS ix_signal_reports_status ON signal_reports (status);

CREATE TABLE IF NOT EXISTS signal_scan_strategy_runs (
	id UUID NOT NULL,
	scan_id UUID NOT NULL,
	strategy_id UUID NOT NULL,
	strategy_type VARCHAR(32) NOT NULL,
	version INTEGER NOT NULL,
	params_hash VARCHAR(64) NOT NULL,
	algorithm_revision VARCHAR(64) NOT NULL,
	runtime JSONB NOT NULL,
	status VARCHAR(24) NOT NULL,
	coverage JSONB NOT NULL,
	error TEXT,
	PRIMARY KEY (id),
	CONSTRAINT uq_signal_strategy_instance UNIQUE (scan_id, strategy_id),
	FOREIGN KEY(scan_id) REFERENCES signal_scan_runs (id)
);

CREATE INDEX IF NOT EXISTS ix_signal_scan_strategy_runs_scan_id ON signal_scan_strategy_runs (scan_id);

CREATE TABLE IF NOT EXISTS signal_observations (
	id UUID NOT NULL,
	scan_id UUID NOT NULL,
	strategy_run_id UUID NOT NULL,
	instrument_id BIGINT NOT NULL,
	symbol_as_of VARCHAR(64) NOT NULL,
	session_date DATE NOT NULL,
	confirmed_at TIMESTAMP WITH TIME ZONE,
	event_type VARCHAR(128) NOT NULL,
	event_kind VARCHAR(16) NOT NULL,
	direction VARCHAR(16),
	event_key VARCHAR(64) NOT NULL,
	passes_signal_filters BOOLEAN NOT NULL,
	strength JSONB,
	strategy_rank INTEGER,
	evidence JSONB NOT NULL,
	snapshot_ref VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_signal_observation_event UNIQUE (strategy_run_id, event_key),
	FOREIGN KEY(scan_id) REFERENCES signal_scan_runs (id),
	FOREIGN KEY(strategy_run_id) REFERENCES signal_scan_strategy_runs (id)
);

CREATE INDEX IF NOT EXISTS ix_signal_observations_instrument_id ON signal_observations (instrument_id);

CREATE INDEX IF NOT EXISTS ix_signal_observations_scan_id ON signal_observations (scan_id);

CREATE INDEX IF NOT EXISTS ix_signal_observations_strategy_run_id ON signal_observations (strategy_run_id);

CREATE TABLE IF NOT EXISTS signal_report_deliveries (
	id UUID NOT NULL,
	report_id UUID NOT NULL,
	recipient VARCHAR(320) NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	claimed_by VARCHAR(128),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_signal_report_recipient UNIQUE (report_id, recipient),
	FOREIGN KEY(report_id) REFERENCES signal_reports (id)
);

CREATE INDEX IF NOT EXISTS ix_signal_report_deliveries_report_id ON signal_report_deliveries (report_id);

CREATE INDEX IF NOT EXISTS ix_signal_report_deliveries_status ON signal_report_deliveries (status);

CREATE TABLE IF NOT EXISTS signal_report_render_jobs (
	id UUID NOT NULL,
	report_id UUID NOT NULL,
	status VARCHAR(24) NOT NULL,
	attempts INTEGER NOT NULL,
	claimed_by VARCHAR(128),
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (report_id),
	FOREIGN KEY(report_id) REFERENCES signal_reports (id)
);

CREATE INDEX IF NOT EXISTS ix_signal_report_render_jobs_status ON signal_report_render_jobs (status);
COMMIT;
