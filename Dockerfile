FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"

# Mount a volume at /data holding config.toml; the CRM database is written next to it.
ENV SALES_AGENT_CONFIG=/data/config.toml
VOLUME /data
EXPOSE 8000

# Set ANTHROPIC_API_KEY and SALES_AGENT_PASSWORD when you run the container.
CMD ["sales-agent", "web", "--host", "0.0.0.0"]
