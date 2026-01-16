#!/usr/bin/env python3
"""
Local JIRA Viewer - Browse exported JIRA tickets from SQLite database

Usage:
    python jira_viewer.py [--db DATABASE] [--port PORT]

Then open http://localhost:5000 in your browser
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path

try:
    from flask import Flask, render_template_string, request, g
except ImportError:
    print("Flask is required. Install with: pip install flask")
    exit(1)

app = Flask(__name__)
app.config["DATABASES"] = []  # List of database paths

# HTML Templates
BASE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ title }} - JIRA Viewer</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0; padding: 20px; background: #f4f5f7;
            color: #172b4d;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #0052cc; margin-bottom: 20px; }
        a { color: #0052cc; text-decoration: none; }
        a:hover { text-decoration: underline; }

        /* Search */
        .search-box {
            background: white; padding: 20px; border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px;
        }
        .search-box input[type="text"] {
            width: 100%; padding: 12px; font-size: 16px;
            border: 2px solid #dfe1e6; border-radius: 4px;
        }
        .search-box input[type="text"]:focus {
            outline: none; border-color: #0052cc;
        }
        .filters { margin-top: 15px; display: flex; gap: 15px; flex-wrap: wrap; }
        .filters select, .filters button {
            padding: 8px 12px; border-radius: 4px; border: 1px solid #dfe1e6;
            background: white; cursor: pointer;
        }
        .filters button { background: #0052cc; color: white; border: none; }
        .filters button:hover { background: #0747a6; }

        /* Ticket list */
        .ticket-list { background: white; border-radius: 8px; overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .ticket-row {
            padding: 15px 20px; border-bottom: 1px solid #ebecf0;
            display: flex; align-items: center; gap: 15px;
        }
        .ticket-row:hover { background: #f4f5f7; }
        .ticket-key {
            font-weight: 600; min-width: 100px;
        }
        .ticket-summary { flex: 1; }
        .ticket-status {
            padding: 4px 8px; border-radius: 3px; font-size: 12px;
            font-weight: 500; text-transform: uppercase;
        }
        .status-done { background: #e3fcef; color: #006644; }
        .status-inprogress { background: #deebff; color: #0747a6; }
        .status-todo { background: #f4f5f7; color: #42526e; }
        .ticket-type {
            font-size: 12px; color: #6b778c; min-width: 80px;
        }
        .ticket-date {
            font-size: 12px; color: #6b778c; min-width: 90px;
        }
        .ticket-header-row {
            background: #f4f5f7; font-weight: 600; font-size: 12px;
            text-transform: uppercase; color: #6b778c;
        }
        .ticket-header-row:hover { background: #f4f5f7; }

        /* Ticket detail */
        .ticket-detail { background: white; border-radius: 8px; padding: 25px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .ticket-header {
            display: flex; align-items: center; gap: 15px;
            margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #ebecf0;
        }
        .ticket-title { font-size: 24px; font-weight: 600; margin: 0; }
        .meta-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px; margin-bottom: 25px;
        }
        .meta-item { }
        .meta-label { font-size: 12px; color: #6b778c; text-transform: uppercase; margin-bottom: 4px; }
        .meta-value { font-size: 14px; }

        .section { margin-top: 25px; }
        .section-title {
            font-size: 14px; font-weight: 600; color: #6b778c;
            text-transform: uppercase; margin-bottom: 15px;
            padding-bottom: 10px; border-bottom: 2px solid #ebecf0;
        }
        .description {
            background: #f4f5f7; padding: 15px; border-radius: 4px;
            white-space: pre-wrap; line-height: 1.6;
        }

        /* Comments */
        .comment {
            padding: 15px; border: 1px solid #ebecf0; border-radius: 4px;
            margin-bottom: 10px;
        }
        .comment-header {
            display: flex; justify-content: space-between;
            margin-bottom: 10px; font-size: 13px; color: #6b778c;
        }
        .comment-author { font-weight: 600; color: #172b4d; }
        .comment-body { white-space: pre-wrap; line-height: 1.5; }

        /* Changelog */
        .changelog-item {
            padding: 10px 0; border-bottom: 1px solid #ebecf0;
            font-size: 13px;
        }
        .changelog-item:last-child { border-bottom: none; }
        .change-field { font-weight: 600; }
        .change-from { color: #de350b; text-decoration: line-through; }
        .change-to { color: #00875a; }
        .change-meta { color: #6b778c; font-size: 12px; margin-top: 4px; }

        /* Pagination */
        .pagination {
            display: flex; justify-content: center; gap: 10px;
            margin-top: 20px;
        }
        .pagination a, .pagination span {
            padding: 8px 12px; border-radius: 4px; background: white;
            border: 1px solid #dfe1e6;
        }
        .pagination a:hover { background: #f4f5f7; text-decoration: none; }
        .pagination .current { background: #0052cc; color: white; border-color: #0052cc; }

        /* Stats */
        .stats {
            display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap;
        }
        .stat-card {
            background: white; padding: 15px 20px; border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 150px;
        }
        .stat-value { font-size: 28px; font-weight: 600; color: #0052cc; }
        .stat-label { font-size: 13px; color: #6b778c; }

        .back-link { margin-bottom: 20px; display: inline-block; }

        /* Labels */
        .labels { display: flex; gap: 5px; flex-wrap: wrap; }
        .label {
            background: #dfe1e6; padding: 2px 8px; border-radius: 3px;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        {{ content|safe }}
    </div>
</body>
</html>
"""

HOME_CONTENT = """
<h1>JIRA Viewer</h1>

<div class="stats">
    <div class="stat-card">
        <div class="stat-value">{{ stats.tickets }}</div>
        <div class="stat-label">Total Tickets</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{{ stats.comments }}</div>
        <div class="stat-label">Comments</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{{ stats.projects }}</div>
        <div class="stat-label">Projects</div>
    </div>
</div>

<div class="search-box">
    <form method="GET" action="/search">
        <input type="text" name="q" placeholder="Search tickets by key, summary, or description..."
               value="{{ query or '' }}">
        <div class="filters">
            <select name="status">
                <option value="">All Statuses</option>
                {% for s in statuses %}
                <option value="{{ s }}" {{ 'selected' if status == s else '' }}>{{ s }}</option>
                {% endfor %}
            </select>
            <select name="project">
                <option value="">All Projects</option>
                {% for p in projects %}
                <option value="{{ p }}" {{ 'selected' if project == p else '' }}>{{ p }}</option>
                {% endfor %}
            </select>
            <select name="type">
                <option value="">All Types</option>
                {% for t in types %}
                <option value="{{ t }}" {{ 'selected' if issue_type == t else '' }}>{{ t }}</option>
                {% endfor %}
            </select>
            <button type="submit">Search</button>
        </div>
    </form>
</div>

<div class="ticket-list">
    <div class="ticket-row ticket-header-row">
        <span class="ticket-type">Type</span>
        <span class="ticket-key">Key</span>
        <span class="ticket-summary">Summary</span>
        <span class="ticket-date">Created</span>
        <span class="ticket-date">Updated</span>
        <span class="ticket-status">Status</span>
    </div>
    {% for ticket in tickets %}
    <div class="ticket-row">
        <span class="ticket-type">{{ ticket.issue_type or 'Task' }}</span>
        <a href="/ticket/{{ ticket.key }}" class="ticket-key">{{ ticket.key }}</a>
        <span class="ticket-summary">{{ ticket.summary }}</span>
        <span class="ticket-date">{{ ticket.created[:10] if ticket.created else '-' }}</span>
        <span class="ticket-date">{{ ticket.updated[:10] if ticket.updated else '-' }}</span>
        <span class="ticket-status status-{{ ticket.status_category|lower|replace(' ', '') if ticket.status_category else 'todo' }}">
            {{ ticket.status or 'Unknown' }}
        </span>
    </div>
    {% else %}
    <div class="ticket-row">No tickets found</div>
    {% endfor %}
</div>

{% if total_pages > 1 %}
<div class="pagination">
    {% if page > 1 %}
    <a href="?{{ query_string }}&page={{ page - 1 }}">&laquo; Previous</a>
    {% endif %}

    {% for p in range(1, total_pages + 1) %}
        {% if p == page %}
        <span class="current">{{ p }}</span>
        {% elif p <= 3 or p > total_pages - 3 or (p >= page - 2 and p <= page + 2) %}
        <a href="?{{ query_string }}&page={{ p }}">{{ p }}</a>
        {% elif p == 4 or p == total_pages - 3 %}
        <span>...</span>
        {% endif %}
    {% endfor %}

    {% if page < total_pages %}
    <a href="?{{ query_string }}&page={{ page + 1 }}">Next &raquo;</a>
    {% endif %}
</div>
{% endif %}
"""

TICKET_CONTENT = """
<a href="/" class="back-link">&larr; Back to list</a>

<div class="ticket-detail">
    <div class="ticket-header">
        <span class="ticket-status status-{{ ticket.status_category|lower|replace(' ', '') if ticket.status_category else 'todo' }}">
            {{ ticket.status or 'Unknown' }}
        </span>
        <h1 class="ticket-title">{{ ticket.key }}: {{ ticket.summary }}</h1>
    </div>

    <div class="meta-grid">
        <div class="meta-item">
            <div class="meta-label">Type</div>
            <div class="meta-value">{{ ticket.issue_type or 'Unknown' }}</div>
        </div>
        <div class="meta-item">
            <div class="meta-label">Priority</div>
            <div class="meta-value">{{ ticket.priority or 'None' }}</div>
        </div>
        <div class="meta-item">
            <div class="meta-label">Project</div>
            <div class="meta-value">{{ ticket.project_name }} ({{ ticket.project_key }})</div>
        </div>
        <div class="meta-item">
            <div class="meta-label">Assignee</div>
            <div class="meta-value">{{ ticket.assignee_name or 'Unassigned' }}</div>
        </div>
        <div class="meta-item">
            <div class="meta-label">Reporter</div>
            <div class="meta-value">{{ ticket.reporter_name or 'Unknown' }}</div>
        </div>
        <div class="meta-item">
            <div class="meta-label">Created</div>
            <div class="meta-value">{{ ticket.created[:10] if ticket.created else 'Unknown' }}</div>
        </div>
        <div class="meta-item">
            <div class="meta-label">Updated</div>
            <div class="meta-value">{{ ticket.updated[:10] if ticket.updated else 'Unknown' }}</div>
        </div>
        <div class="meta-item">
            <div class="meta-label">Resolution</div>
            <div class="meta-value">{{ ticket.resolution or 'Unresolved' }}</div>
        </div>
    </div>

    {% if labels %}
    <div class="meta-item">
        <div class="meta-label">Labels</div>
        <div class="labels">
            {% for label in labels %}
            <span class="label">{{ label }}</span>
            {% endfor %}
        </div>
    </div>
    {% endif %}

    <div class="section">
        <div class="section-title">Description</div>
        <div class="description">{{ ticket.description or 'No description' }}</div>
    </div>

    {% if comments %}
    <div class="section">
        <div class="section-title">Comments ({{ comments|length }})</div>
        {% for comment in comments %}
        <div class="comment">
            <div class="comment-header">
                <span class="comment-author">{{ comment.author_name or 'Unknown' }}</span>
                <span>{{ comment.created[:16] if comment.created else '' }}</span>
            </div>
            <div class="comment-body">{{ comment.body }}</div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if changelog %}
    <div class="section">
        <div class="section-title">History ({{ changelog|length }})</div>
        {% for change in changelog %}
        <div class="changelog-item">
            <span class="change-field">{{ change.field }}</span>:
            {% if change.from_value %}
            <span class="change-from">{{ change.from_value }}</span> &rarr;
            {% endif %}
            <span class="change-to">{{ change.to_value }}</span>
            <div class="change-meta">
                {{ change.author_name or 'Unknown' }} - {{ change.created[:16] if change.created else '' }}
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}
</div>
"""


def get_all_dbs():
    """Get connections to all databases."""
    if "dbs" not in g:
        g.dbs = []
        for db_path in app.config["DATABASES"]:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            g.dbs.append(conn)
    return g.dbs


def get_db_for_ticket(ticket_key):
    """Find which database contains a specific ticket."""
    for db in get_all_dbs():
        cursor = db.cursor()
        cursor.execute("SELECT key FROM tickets WHERE key = ?", (ticket_key,))
        if cursor.fetchone():
            return db
    return None


@app.teardown_appcontext
def close_db(exception):
    """Close all database connections."""
    dbs = g.pop("dbs", None)
    if dbs:
        for db in dbs:
            db.close()


def get_stats():
    """Get combined database statistics."""
    tickets = 0
    comments = 0
    projects = set()

    for db in get_all_dbs():
        cursor = db.cursor()

        cursor.execute("SELECT COUNT(*) FROM tickets")
        tickets += cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM comments")
        comments += cursor.fetchone()[0]

        cursor.execute("SELECT DISTINCT project_key FROM tickets WHERE project_key IS NOT NULL")
        for row in cursor.fetchall():
            projects.add(row[0])

    return {"tickets": tickets, "comments": comments, "projects": len(projects)}


def get_filter_options():
    """Get unique values for filters from all databases."""
    statuses = set()
    projects = set()
    types = set()

    for db in get_all_dbs():
        cursor = db.cursor()

        cursor.execute("SELECT DISTINCT status FROM tickets WHERE status IS NOT NULL")
        for row in cursor.fetchall():
            statuses.add(row[0])

        cursor.execute("SELECT DISTINCT project_key FROM tickets WHERE project_key IS NOT NULL")
        for row in cursor.fetchall():
            projects.add(row[0])

        cursor.execute("SELECT DISTINCT issue_type FROM tickets WHERE issue_type IS NOT NULL")
        for row in cursor.fetchall():
            types.add(row[0])

    return sorted(statuses), sorted(projects), sorted(types)


@app.route("/")
def home():
    """Home page with recent tickets."""
    return search()


@app.route("/search")
def search():
    """Search tickets across all databases."""
    query = request.args.get("q", "")
    status = request.args.get("status", "")
    project = request.args.get("project", "")
    issue_type = request.args.get("type", "")
    page = int(request.args.get("page", 1))
    per_page = 50

    # Get tickets from all databases, combine and sort
    all_tickets = []

    for db in get_all_dbs():
        cursor = db.cursor()

        # Check if FTS table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tickets_fts'")
        has_fts = cursor.fetchone() is not None

        if query and has_fts:
            # Use FTS for text search (much faster)
            fts_query = f'"{query}"' if ' ' in query else f'{query}*'
            sql = """
                SELECT t.key, t.summary, t.status, t.status_category, t.issue_type,
                       t.project_key, t.assignee_name, t.created, t.updated
                FROM tickets t
                JOIN tickets_fts fts ON t.key = fts.key
                WHERE tickets_fts MATCH ?
            """
            params = [fts_query]

            if status:
                sql += " AND t.status = ?"
                params.append(status)
            if project:
                sql += " AND t.project_key = ?"
                params.append(project)
            if issue_type:
                sql += " AND t.issue_type = ?"
                params.append(issue_type)

            cursor.execute(sql, params)
        else:
            # Fallback to regular search
            conditions = []
            params = []

            if query:
                conditions.append("(key LIKE ? OR summary LIKE ? OR description LIKE ?)")
                params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])

            if status:
                conditions.append("status = ?")
                params.append(status)

            if project:
                conditions.append("project_key = ?")
                params.append(project)

            if issue_type:
                conditions.append("issue_type = ?")
                params.append(issue_type)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            cursor.execute(f"""
                SELECT key, summary, status, status_category, issue_type,
                       project_key, assignee_name, created, updated
                FROM tickets
                WHERE {where_clause}
            """, params)

        all_tickets.extend([dict(row) for row in cursor.fetchall()])

    # Sort by updated descending
    all_tickets.sort(key=lambda x: x.get("updated") or "", reverse=True)

    # Paginate
    total = len(all_tickets)
    total_pages = (total + per_page - 1) // per_page
    offset = (page - 1) * per_page
    tickets = all_tickets[offset:offset + per_page]

    # Build query string for pagination
    query_parts = []
    if query:
        query_parts.append(f"q={query}")
    if status:
        query_parts.append(f"status={status}")
    if project:
        query_parts.append(f"project={project}")
    if issue_type:
        query_parts.append(f"type={issue_type}")
    query_string = "&".join(query_parts)

    statuses, projects, types = get_filter_options()

    content = render_template_string(
        HOME_CONTENT,
        tickets=tickets,
        stats=get_stats(),
        statuses=statuses,
        projects=projects,
        types=types,
        query=query,
        status=status,
        project=project,
        issue_type=issue_type,
        page=page,
        total_pages=total_pages,
        query_string=query_string,
    )

    return render_template_string(BASE_TEMPLATE, title="Search", content=content)


@app.route("/ticket/<key>")
def ticket_detail(key):
    """Show ticket details."""
    # Find the database containing this ticket
    db = get_db_for_ticket(key)

    if not db:
        return "Ticket not found", 404

    cursor = db.cursor()

    # Get ticket
    cursor.execute("SELECT * FROM tickets WHERE key = ?", (key,))
    ticket = cursor.fetchone()

    # Get comments
    cursor.execute("""
        SELECT * FROM comments WHERE ticket_key = ? ORDER BY created ASC
    """, (key,))
    comments = cursor.fetchall()

    # Get changelog
    cursor.execute("""
        SELECT * FROM changelog WHERE ticket_key = ? ORDER BY created DESC
    """, (key,))
    changelog = cursor.fetchall()

    # Parse labels
    labels = []
    if ticket["labels"]:
        try:
            labels = json.loads(ticket["labels"])
        except:
            pass

    content = render_template_string(
        TICKET_CONTENT,
        ticket=ticket,
        comments=comments,
        changelog=changelog,
        labels=labels,
    )

    return render_template_string(BASE_TEMPLATE, title=key, content=content)


def main():
    parser = argparse.ArgumentParser(description="Local JIRA ticket viewer")
    parser.add_argument("--db", nargs="+", default=None, help="SQLite database file(s)")
    parser.add_argument("--db-dir", help="Directory containing .db files (loads all)")
    parser.add_argument("--port", type=int, default=5000, help="Port to run on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    # Collect database files
    db_files = []

    # Check environment variable first (for Docker)
    env_dbs = os.environ.get("JIRA_DB_PATH", "")
    if env_dbs:
        db_files.extend(env_dbs.split(":"))

    # Add databases from --db argument
    if args.db:
        db_files.extend(args.db)

    # Add databases from --db-dir
    if args.db_dir:
        db_dir = Path(args.db_dir)
        if db_dir.exists():
            db_files.extend([str(f) for f in db_dir.glob("*.db")])

    # Default to jira.db if nothing specified
    if not db_files:
        db_files = ["jira.db"]

    # Verify all database files exist
    valid_dbs = []
    for db_path in db_files:
        if Path(db_path).exists():
            valid_dbs.append(db_path)
        else:
            print(f"Warning: Database not found: {db_path}")

    if not valid_dbs:
        print("No valid database files found!")
        print("Run jira_scraper.py first to export tickets")
        exit(1)

    app.config["DATABASES"] = valid_dbs

    print(f"Starting JIRA Viewer...")
    print(f"Databases ({len(valid_dbs)}):")
    for db in valid_dbs:
        print(f"  - {db}")
    print(f"Open http://{args.host}:{args.port} in your browser")
    print()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
