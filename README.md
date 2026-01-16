# jira-scrappy-view

Export JIRA tickets to SQLite databases and browse them locally with a web interface.

## Features

- **Scraper**: Export JIRA tickets to SQLite with full history, comments, and changelog
- **Viewer**: Local web UI to search and browse tickets (works offline)
- **Fast search**: Full-text search (FTS5) for instant results
- **Multi-project**: Scrape and view multiple projects
- **LLM-ready**: SQLite databases can be used with RAG/LLM applications
- **Docker support**: Run the viewer in a container

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure JIRA credentials

Create a `.env` file or set environment variables:

```bash
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=your.email@company.com
JIRA_API_TOKEN=your_api_token
```

Get your API token at: https://id.atlassian.com/manage-profile/security/api-tokens

### 3. Scrape a project

```bash
# Single project
python jira_scraper.py --project PROJ --db

# Multiple projects
python jira_scraper.py --projects PROJ1 PROJ2 PROJ3 --db

# All projects into one database
python jira_scraper.py --projects PROJ1 PROJ2 --db all_projects.db

# All accessible projects
python jira_scraper.py --all-projects --db
```

### 4. View tickets

```bash
# Single database
python jira_viewer.py --db PROJ.db

# Multiple databases
python jira_viewer.py --db PROJ1.db PROJ2.db

# All databases in a directory
python jira_viewer.py --db-dir ./databases/
```

Open http://localhost:5000 in your browser.

## Docker

### Build and run

```bash
docker compose up -d
```

### Configure databases

Edit `docker-compose.yml` to mount your database files:

```yaml
volumes:
  - ./PROJECT1.db:/data/PROJECT1.db:ro
  - ./PROJECT2.db:/data/PROJECT2.db:ro
environment:
  - JIRA_DB_PATH=/data/PROJECT1.db:/data/PROJECT2.db
```

## CLI Reference

### Scraper

```bash
# Test connection
python jira_scraper.py --test

# List available projects
python jira_scraper.py --list-projects

# Scrape with date filter
python jira_scraper.py --project PROJ --since 2024-01-01 --db

# Custom JQL query
python jira_scraper.py --jql "project = PROJ AND status = Done" --db custom.db

# Optimize existing database (add indexes, rebuild FTS)
python jira_scraper.py --optimize database.db
```

### Viewer

```bash
python jira_viewer.py --db database.db --port 8080 --host 0.0.0.0
```

## Using the Database

The SQLite database can be used directly for analysis, LLM applications, or custom tools.

### Schema

- `tickets` - Main ticket data (key, summary, description, status, assignee, etc.)
- `comments` - All comments linked to tickets
- `changelog` - Full history of field changes
- `issue_links` - Links between tickets
- `subtasks` - Subtask relationships
- `tickets_fts` - Full-text search index

### Python Example

```python
import sqlite3
import pandas as pd

# Connect to database
conn = sqlite3.connect("PROJECT.db")

# Load tickets into DataFrame
df = pd.read_sql_query("SELECT * FROM tickets", conn)

# Search with FTS
results = pd.read_sql_query("""
    SELECT t.* FROM tickets t
    JOIN tickets_fts fts ON t.key = fts.key
    WHERE tickets_fts MATCH 'bug'
""", conn)

# Get ticket with comments
ticket = pd.read_sql_query("SELECT * FROM tickets WHERE key = 'PROJ-123'", conn)
comments = pd.read_sql_query("SELECT * FROM comments WHERE ticket_key = 'PROJ-123'", conn)
```

### SQL Examples

```sql
-- Find all open bugs
SELECT key, summary, assignee_name
FROM tickets
WHERE status != 'Done' AND issue_type = 'Bug';

-- Tickets updated in last 30 days
SELECT key, summary, updated
FROM tickets
WHERE updated >= date('now', '-30 days');

-- Most active contributors
SELECT assignee_name, COUNT(*) as ticket_count
FROM tickets
GROUP BY assignee_name
ORDER BY ticket_count DESC;

-- Full-text search
SELECT t.key, t.summary
FROM tickets t
JOIN tickets_fts ON t.key = tickets_fts.key
WHERE tickets_fts MATCH 'authentication error';
```

### LLM/RAG Integration

The databases work well with LangChain, LlamaIndex, or custom RAG pipelines:

```python
from langchain.document_loaders import SQLDatabaseLoader
from langchain.vectorstores import Chroma

# Load tickets as documents
loader = SQLDatabaseLoader(
    database="sqlite:///PROJECT.db",
    query="SELECT key, summary, description FROM tickets"
)
documents = loader.load()

# Create vector store for semantic search
vectorstore = Chroma.from_documents(documents, embedding_function)
```

## License

MIT
