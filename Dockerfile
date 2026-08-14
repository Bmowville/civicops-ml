# syntax=docker/dockerfile:1.12
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY requirements-build-lock.txt requirements-lock.txt pyproject.toml README.md LICENSE ./
RUN pip install --no-deps -r requirements-build-lock.txt \
    && pip install --no-deps -r requirements-lock.txt
COPY src ./src
RUN pip install --no-deps --no-build-isolation .

FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CIVICOPS_ENVIRONMENT=production \
    CIVICOPS_AUTO_MIGRATE=false \
    CIVICOPS_MODEL_PATH=/app/models/candidate.joblib \
    CIVICOPS_CALIBRATOR_PATH=/app/models/calibrator.joblib \
    CIVICOPS_DIAGNOSTICS_PATH=/app/reports/diagnostics.json \
    CIVICOPS_BASELINE_PATH=/app/reports/baseline_metrics.json

RUN groupadd --gid 10001 civicops \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin civicops

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=10001:10001 models/candidate.joblib models/calibrator.joblib ./models/
COPY --chown=10001:10001 reports/diagnostics.json reports/baseline_metrics.json ./reports/

USER 10001:10001
EXPOSE 8000

CMD ["civicops-ml", "--host", "0.0.0.0", "--port", "8000"]
