CREATE SCHEMA IF NOT EXISTS civicops;

CREATE TABLE IF NOT EXISTS civicops.predictions (
    prediction_id UUID PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_json JSONB NOT NULL,
    probability DOUBLE PRECISION NOT NULL CHECK (probability >= 0 AND probability <= 1),
    review_tier TEXT NOT NULL CHECK (review_tier IN ('standard_review', 'priority_review')),
    model_sha256 CHAR(64) NOT NULL,
    actor_id TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    actor_role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS civicops.reviews (
    review_id UUID PRIMARY KEY,
    prediction_id UUID NOT NULL UNIQUE
        REFERENCES civicops.predictions(prediction_id) ON DELETE RESTRICT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    action TEXT NOT NULL CHECK (action IN ('escalate', 'monitor', 'standard_process')),
    rationale TEXT NOT NULL CHECK (length(rationale) BETWEEN 5 AND 500),
    actor_id TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    actor_role TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS predictions_recorded_at_idx
    ON civicops.predictions (recorded_at DESC);
CREATE INDEX IF NOT EXISTS predictions_actor_id_idx
    ON civicops.predictions (actor_id);
CREATE INDEX IF NOT EXISTS reviews_recorded_at_idx
    ON civicops.reviews (recorded_at DESC);
