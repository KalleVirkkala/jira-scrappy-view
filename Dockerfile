FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY jira_viewer.py .

# Create directory for database
RUN mkdir -p /data

EXPOSE 5000

# Database path can be set via environment variable
ENV JIRA_DB_PATH=/data/jira.db

# Run the viewer
CMD python jira_viewer.py --db "$JIRA_DB_PATH" --host 0.0.0.0
