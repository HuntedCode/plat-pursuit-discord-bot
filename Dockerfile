# Build
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
ENV PYTHONUNBUFFERED=1
# Run as an unprivileged user: the bot writes nothing to disk, so it needs no write access.
RUN useradd --system --no-create-home --uid 10001 platbot
USER platbot
CMD ["python", "bot.py"]