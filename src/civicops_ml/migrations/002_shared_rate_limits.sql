CREATE TABLE civicops.rate_limits (
    key_sha256 CHAR(64) PRIMARY KEY,
    event_times DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX rate_limits_expiry_idx ON civicops.rate_limits (expires_at);
