FROM python:3.12-slim

# One async worker per instance is enough: Gemini calls wait on the network, and Cloud Run
# adds instances under load. Data lives in Cloud Storage (DATA_BUCKET), not on local disk.
# TZ keeps evaluation dates in Thai time (Cloud Run runs in UTC).
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    WEB_CONCURRENCY=1 \
    TZ=Asia/Bangkok

# Create a non-root user
RUN groupadd -r -g 1000 appgroup && useradd -r -u 1000 -g appgroup appuser

# Set up the application directory
WORKDIR /usr/src/app

# Copy dependencies and install
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip

# Copy the application code
COPY . .

# log/ is only used when DATA_BUCKET is not set (local runs)
RUN mkdir -p log && chown -R appuser:appgroup /usr/src/app

# Switch to non-root user
USER appuser

EXPOSE 8080

# Cloud Run sets PORT; gunicorn reads the worker count from WEB_CONCURRENCY
# --timeout 0 lets Cloud Run's request timeout handle slow Gemini calls instead of killing workers
CMD exec gunicorn -k uvicorn.workers.UvicornWorker -b "0.0.0.0:${PORT}" --timeout 0 \
    --access-logfile - --error-logfile - main:app
