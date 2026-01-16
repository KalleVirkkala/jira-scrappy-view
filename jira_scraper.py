#!/usr/bin/env python3
"""
JIRA Cloud Scraper - Export tickets to JSON files

Usage:
    python jira_scraper.py --project PROJECT_KEY
    python jira_scraper.py --jql "project = PROJ AND created >= -30d"
    python jira_scraper.py --project PROJ --since 2024-01-01

Environment variables (or use .env file):
    JIRA_URL: Your JIRA Cloud URL (e.g., https://yourcompany.atlassian.net)
    JIRA_EMAIL: Your Atlassian account email
    JIRA_API_TOKEN: Your JIRA API token

To create an API token:
    1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
    2. Click "Create API token"
    3. Copy the token and set it as JIRA_API_TOKEN
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

# Load .env file FIRST (before reading environment variables)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional

try:
    import requests
except ImportError:
    print("Error: requests library not found. Install with: pip install requests")
    sys.exit(1)

# JIRA Configuration - set via environment variables or .env file
JIRA_URL = os.environ.get("JIRA_URL", "")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")


@dataclass
class JiraConfig:
    url: str
    email: str
    api_token: str

    @classmethod
    def from_env(cls) -> "JiraConfig":
        url = JIRA_URL
        email = JIRA_EMAIL
        api_token = JIRA_API_TOKEN

        # Validate required settings
        missing = []
        if not url:
            missing.append("JIRA_URL")
        if not email:
            missing.append("JIRA_EMAIL")
        if not api_token:
            missing.append("JIRA_API_TOKEN")

        if missing:
            print("ERROR: Missing required environment variables:")
            for var in missing:
                print(f"  - {var}")
            print("\nSet them in a .env file or as environment variables.")
            print("See .env.example for reference.")
            sys.exit(1)

        return cls(url=url.rstrip("/"), email=email, api_token=api_token)


class JiraDatabase:
    """SQLite database for storing JIRA tickets."""

    def __init__(self, db_path: str = "jira.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create the database schema."""
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                key TEXT PRIMARY KEY,
                id TEXT,
                summary TEXT,
                description TEXT,
                description_raw TEXT,
                status TEXT,
                status_category TEXT,
                priority TEXT,
                issue_type TEXT,
                is_subtask INTEGER,
                project_key TEXT,
                project_name TEXT,
                creator_id TEXT,
                creator_name TEXT,
                creator_email TEXT,
                reporter_id TEXT,
                reporter_name TEXT,
                reporter_email TEXT,
                assignee_id TEXT,
                assignee_name TEXT,
                assignee_email TEXT,
                created TEXT,
                updated TEXT,
                resolved TEXT,
                resolution TEXT,
                labels TEXT,
                components TEXT,
                fix_versions TEXT,
                affects_versions TEXT,
                parent_key TEXT,
                parent_summary TEXT,
                custom_fields TEXT,
                raw_json TEXT,
                exported_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                ticket_key TEXT,
                author_id TEXT,
                author_name TEXT,
                author_email TEXT,
                body TEXT,
                body_raw TEXT,
                created TEXT,
                updated TEXT,
                FOREIGN KEY (ticket_key) REFERENCES tickets(key)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS changelog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_key TEXT,
                field TEXT,
                field_type TEXT,
                from_value TEXT,
                to_value TEXT,
                author_id TEXT,
                author_name TEXT,
                author_email TEXT,
                created TEXT,
                FOREIGN KEY (ticket_key) REFERENCES tickets(key)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS issue_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_key TEXT,
                link_type TEXT,
                inward_key TEXT,
                outward_key TEXT,
                FOREIGN KEY (ticket_key) REFERENCES tickets(key)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subtasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_key TEXT,
                subtask_key TEXT,
                subtask_summary TEXT,
                FOREIGN KEY (ticket_key) REFERENCES tickets(key)
            )
        """)

        # Create indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_project ON tickets(project_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets(assignee_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_updated ON tickets(updated)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_issue_type ON tickets(issue_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_reporter ON tickets(reporter_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_changelog_ticket ON changelog(ticket_key)")

        # Compound indexes for common filter combinations
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_project_status ON tickets(project_key, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_project_updated ON tickets(project_key, updated DESC)")

        # Full-text search table for fast text searching
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5(
                key,
                summary,
                description,
                content='tickets',
                content_rowid='rowid'
            )
        """)

        # Triggers to keep FTS in sync
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS tickets_ai AFTER INSERT ON tickets BEGIN
                INSERT INTO tickets_fts(rowid, key, summary, description)
                VALUES (NEW.rowid, NEW.key, NEW.summary, NEW.description);
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS tickets_ad AFTER DELETE ON tickets BEGIN
                INSERT INTO tickets_fts(tickets_fts, rowid, key, summary, description)
                VALUES ('delete', OLD.rowid, OLD.key, OLD.summary, OLD.description);
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS tickets_au AFTER UPDATE ON tickets BEGIN
                INSERT INTO tickets_fts(tickets_fts, rowid, key, summary, description)
                VALUES ('delete', OLD.rowid, OLD.key, OLD.summary, OLD.description);
                INSERT INTO tickets_fts(rowid, key, summary, description)
                VALUES (NEW.rowid, NEW.key, NEW.summary, NEW.description);
            END
        """)

        self.conn.commit()

    def rebuild_fts(self):
        """Rebuild the full-text search index."""
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO tickets_fts(tickets_fts) VALUES('rebuild')")
        self.conn.commit()

    def insert_ticket(self, ticket: dict):
        """Insert or update a ticket in the database."""
        cursor = self.conn.cursor()

        # Extract user info helpers
        def get_user_field(user_dict, field):
            if user_dict is None:
                return None
            return user_dict.get(field)

        cursor.execute("""
            INSERT OR REPLACE INTO tickets (
                key, id, summary, description, description_raw,
                status, status_category, priority, issue_type, is_subtask,
                project_key, project_name,
                creator_id, creator_name, creator_email,
                reporter_id, reporter_name, reporter_email,
                assignee_id, assignee_name, assignee_email,
                created, updated, resolved, resolution,
                labels, components, fix_versions, affects_versions,
                parent_key, parent_summary, custom_fields, raw_json, exported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticket.get("key"),
            ticket.get("id"),
            ticket.get("summary"),
            ticket.get("description"),
            json.dumps(ticket.get("descriptionRaw")),
            ticket.get("status", {}).get("name") if ticket.get("status") else None,
            ticket.get("status", {}).get("category") if ticket.get("status") else None,
            ticket.get("priority", {}).get("name") if ticket.get("priority") else None,
            ticket.get("issueType", {}).get("name") if ticket.get("issueType") else None,
            1 if ticket.get("issueType", {}).get("subtask") else 0,
            ticket.get("project", {}).get("key") if ticket.get("project") else None,
            ticket.get("project", {}).get("name") if ticket.get("project") else None,
            get_user_field(ticket.get("creator"), "accountId"),
            get_user_field(ticket.get("creator"), "displayName"),
            get_user_field(ticket.get("creator"), "emailAddress"),
            get_user_field(ticket.get("reporter"), "accountId"),
            get_user_field(ticket.get("reporter"), "displayName"),
            get_user_field(ticket.get("reporter"), "emailAddress"),
            get_user_field(ticket.get("assignee"), "accountId"),
            get_user_field(ticket.get("assignee"), "displayName"),
            get_user_field(ticket.get("assignee"), "emailAddress"),
            ticket.get("created"),
            ticket.get("updated"),
            ticket.get("resolved"),
            ticket.get("resolution"),
            json.dumps(ticket.get("labels", [])),
            json.dumps(ticket.get("components", [])),
            json.dumps(ticket.get("fixVersions", [])),
            json.dumps(ticket.get("affectsVersions", [])),
            ticket.get("parent", {}).get("key") if ticket.get("parent") else None,
            ticket.get("parent", {}).get("summary") if ticket.get("parent") else None,
            json.dumps(ticket.get("customFields", {})),
            json.dumps(ticket),
            ticket.get("_exportedAt"),
        ))

        ticket_key = ticket.get("key")

        # Delete existing related data (for updates)
        cursor.execute("DELETE FROM comments WHERE ticket_key = ?", (ticket_key,))
        cursor.execute("DELETE FROM changelog WHERE ticket_key = ?", (ticket_key,))
        cursor.execute("DELETE FROM issue_links WHERE ticket_key = ?", (ticket_key,))
        cursor.execute("DELETE FROM subtasks WHERE ticket_key = ?", (ticket_key,))

        # Insert comments
        for comment in ticket.get("comments", []):
            cursor.execute("""
                INSERT INTO comments (id, ticket_key, author_id, author_name, author_email, body, body_raw, created, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                comment.get("id"),
                ticket_key,
                get_user_field(comment.get("author"), "accountId"),
                get_user_field(comment.get("author"), "displayName"),
                get_user_field(comment.get("author"), "emailAddress"),
                comment.get("body"),
                json.dumps(comment.get("bodyRaw")),
                comment.get("created"),
                comment.get("updated"),
            ))

        # Insert changelog
        for change in ticket.get("changelog", []):
            cursor.execute("""
                INSERT INTO changelog (ticket_key, field, field_type, from_value, to_value, author_id, author_name, author_email, created)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticket_key,
                change.get("field"),
                change.get("fieldtype"),
                change.get("from"),
                change.get("to"),
                get_user_field(change.get("author"), "accountId"),
                get_user_field(change.get("author"), "displayName"),
                get_user_field(change.get("author"), "emailAddress"),
                change.get("created"),
            ))

        # Insert issue links
        for link in ticket.get("links", []):
            cursor.execute("""
                INSERT INTO issue_links (ticket_key, link_type, inward_key, outward_key)
                VALUES (?, ?, ?, ?)
            """, (
                ticket_key,
                link.get("type"),
                link.get("inward"),
                link.get("outward"),
            ))

        # Insert subtasks
        for subtask in ticket.get("subtasks", []):
            cursor.execute("""
                INSERT INTO subtasks (ticket_key, subtask_key, subtask_summary)
                VALUES (?, ?, ?)
            """, (
                ticket_key,
                subtask.get("key"),
                subtask.get("summary"),
            ))

        self.conn.commit()

    def get_stats(self) -> dict:
        """Get database statistics."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tickets")
        tickets = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM comments")
        comments = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM changelog")
        changelog = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT project_key) FROM tickets")
        projects = cursor.fetchone()[0]
        return {
            "tickets": tickets,
            "comments": comments,
            "changelog_entries": changelog,
            "projects": projects,
        }

    def close(self):
        """Close the database connection."""
        self.conn.close()


class JiraScraper:
    """Scrapes JIRA tickets and exports them to JSON files."""

    def __init__(self, config: JiraConfig, output_dir: str = "jira_export"):
        self.config = config
        self.output_dir = Path(output_dir)
        self.session = requests.Session()
        self.session.auth = (config.email, config.api_token)
        self.session.headers.update({"Accept": "application/json"})

    def _api_get(self, endpoint: str, params: dict | None = None, max_retries: int = 5) -> dict[str, Any]:
        """Make a GET request to the JIRA API with retry logic for rate limits."""
        url = urljoin(self.config.url + "/", f"rest/api/3/{endpoint}")

        for attempt in range(max_retries):
            response = self.session.get(url, params=params)

            if response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = int(response.headers.get("Retry-After", 10))
                wait_time = max(retry_after, 2 ** attempt)  # Exponential backoff, minimum from header
                print(f"\n  Rate limited. Waiting {wait_time}s before retry ({attempt + 1}/{max_retries})...")
                time.sleep(wait_time)
                continue

            if response.status_code == 401:
                print("ERROR: Authentication failed (401 Unauthorized)")
                print("Check your JIRA_EMAIL and JIRA_API_TOKEN")
                print(f"Response: {response.text}")
                sys.exit(1)
            elif response.status_code == 403:
                print("ERROR: Access forbidden (403)")
                print("Your token is valid but you don't have permission")
                print(f"Response: {response.text}")
                sys.exit(1)
            elif not response.ok:
                print(f"ERROR: API request failed ({response.status_code})")
                print(f"URL: {url}")
                print(f"Response: {response.text}")
                response.raise_for_status()

            return response.json()

        # If we exhausted retries
        print(f"ERROR: Max retries ({max_retries}) exceeded for {url}")
        sys.exit(1)

    def test_connection(self) -> bool:
        """Test the JIRA connection and show user info."""
        print(f"Testing connection to {self.config.url}...")
        print(f"Using email: {self.config.email}")
        print()

        try:
            user = self._api_get("myself")
            print(f"✓ Connected successfully!")
            print(f"  Account: {user.get('displayName')} ({user.get('emailAddress')})")
            print(f"  Account ID: {user.get('accountId')}")
            print()
            return True
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            return False

    def list_projects(self) -> list[dict]:
        """List all accessible projects."""
        try:
            projects = self._api_get("project")
            return projects
        except Exception:
            return []

    def _paginate(
        self, endpoint: str, params: dict | None = None, key: str = "values", progress: bool = True
    ) -> list[dict]:
        """Paginate through JIRA API results, fetching ALL results."""
        params = dict(params) if params else {}  # Make a copy to avoid mutation
        params["startAt"] = 0
        params["maxResults"] = 100  # Max allowed by JIRA API
        all_results = []
        total = None
        page = 0

        while True:
            page += 1
            data = self._api_get(endpoint, params)

            if key == "issues":
                results = data.get("issues", [])
            else:
                results = data.get(key, [])

            all_results.extend(results)

            # Get total on first request
            if total is None:
                total = data.get("total", len(results))
                if progress:
                    print(f"  Total issues to fetch: {total}")

            # Show progress
            if progress:
                print(f"  Page {page}: fetched {len(results)} issues (total so far: {len(all_results)}/{total})")

            # Check if we've got all results
            if len(all_results) >= total:
                if progress:
                    print(f"  Done: fetched all {total} issues")
                break

            if not results:
                if progress:
                    print(f"  Warning: got empty page but only have {len(all_results)}/{total}")
                break

            params["startAt"] = len(all_results)

        return all_results

    def search_issues(self, jql: str, progress: bool = True) -> list[dict]:
        """Search for issues using JQL with cursor-based pagination."""
        all_issues = []
        next_page_token = None
        page = 0

        while True:
            page += 1
            params = {
                "jql": jql,
                "expand": "changelog,renderedFields",
                "fields": "*all",
                "maxResults": 100,
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token

            data = self._api_get("search/jql", params)
            issues = data.get("issues", [])
            all_issues.extend(issues)

            total = data.get("total", "?")
            if progress:
                print(f"  Page {page}: fetched {len(issues)} issues (total so far: {len(all_issues)}, reported total: {total})")

            # Check for next page using cursor
            next_page_token = data.get("nextPageToken")
            if not next_page_token or not issues:
                if progress:
                    print(f"  Done: fetched {len(all_issues)} issues")
                break

        return all_issues

    def get_issue_comments(self, issue_key: str) -> list[dict]:
        """Get all comments for an issue."""
        try:
            time.sleep(0.1)  # Small delay to avoid rate limiting
            data = self._api_get(f"issue/{issue_key}/comment")
            return data.get("comments", [])
        except requests.HTTPError:
            return []

    def get_issue_transitions(self, issue_key: str) -> list[dict]:
        """Get available transitions for an issue (for reference)."""
        try:
            data = self._api_get(f"issue/{issue_key}/transitions")
            return data.get("transitions", [])
        except requests.HTTPError:
            return []

    def extract_user_info(self, user_data: dict | None) -> dict | None:
        """Extract relevant user information."""
        if not user_data:
            return None
        return {
            "accountId": user_data.get("accountId"),
            "displayName": user_data.get("displayName"),
            "emailAddress": user_data.get("emailAddress"),
        }

    def extract_comment_info(self, comment: dict) -> dict:
        """Extract relevant comment information."""
        body = comment.get("body", {})
        # Handle Atlassian Document Format (ADF)
        if isinstance(body, dict):
            body_text = self._adf_to_text(body)
        else:
            body_text = str(body)

        return {
            "id": comment.get("id"),
            "author": self.extract_user_info(comment.get("author")),
            "body": body_text,
            "bodyRaw": comment.get("body"),
            "created": comment.get("created"),
            "updated": comment.get("updated"),
        }

    def _adf_to_text(self, adf: dict) -> str:
        """Convert Atlassian Document Format to plain text."""
        if not isinstance(adf, dict):
            return str(adf)

        text_parts = []

        def extract_text(node: dict | list | str) -> None:
            if isinstance(node, str):
                text_parts.append(node)
            elif isinstance(node, list):
                for item in node:
                    extract_text(item)
            elif isinstance(node, dict):
                if node.get("type") == "text":
                    text_parts.append(node.get("text", ""))
                elif node.get("type") == "hardBreak":
                    text_parts.append("\n")
                elif node.get("type") == "mention":
                    text_parts.append(f"@{node.get('attrs', {}).get('text', 'user')}")
                content = node.get("content", [])
                if content:
                    extract_text(content)

        extract_text(adf)
        return "".join(text_parts)

    def extract_changelog(self, issue: dict) -> list[dict]:
        """Extract status history from changelog."""
        changelog = issue.get("changelog", {})
        histories = changelog.get("histories", [])

        status_changes = []
        for history in histories:
            for item in history.get("items", []):
                status_changes.append(
                    {
                        "field": item.get("field"),
                        "fieldtype": item.get("fieldtype"),
                        "from": item.get("fromString"),
                        "to": item.get("toString"),
                        "author": self.extract_user_info(history.get("author")),
                        "created": history.get("created"),
                    }
                )

        return status_changes

    def process_issue(self, issue: dict) -> dict:
        """Process a single issue and extract all relevant data."""
        fields = issue.get("fields", {})
        issue_key = issue.get("key")

        # Get comments
        comments = self.get_issue_comments(issue_key)

        # Extract description
        description = fields.get("description", {})
        if isinstance(description, dict):
            description_text = self._adf_to_text(description)
        else:
            description_text = str(description) if description else ""

        processed = {
            "key": issue_key,
            "id": issue.get("id"),
            "self": issue.get("self"),
            "summary": fields.get("summary"),
            "description": description_text,
            "descriptionRaw": fields.get("description"),
            "status": {
                "name": fields.get("status", {}).get("name"),
                "category": fields.get("status", {}).get("statusCategory", {}).get(
                    "name"
                ),
            },
            "priority": {
                "name": fields.get("priority", {}).get("name") if fields.get("priority") else None,
            },
            "issueType": {
                "name": fields.get("issuetype", {}).get("name"),
                "subtask": fields.get("issuetype", {}).get("subtask"),
            },
            "project": {
                "key": fields.get("project", {}).get("key"),
                "name": fields.get("project", {}).get("name"),
            },
            "creator": self.extract_user_info(fields.get("creator")),
            "reporter": self.extract_user_info(fields.get("reporter")),
            "assignee": self.extract_user_info(fields.get("assignee")),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "resolved": fields.get("resolutiondate"),
            "resolution": fields.get("resolution", {}).get("name")
            if fields.get("resolution")
            else None,
            "labels": fields.get("labels", []),
            "components": [c.get("name") for c in fields.get("components", [])],
            "fixVersions": [v.get("name") for v in fields.get("fixVersions", [])],
            "affectsVersions": [v.get("name") for v in fields.get("versions", [])],
            "comments": [self.extract_comment_info(c) for c in comments],
            "changelog": self.extract_changelog(issue),
            "subtasks": [
                {"key": st.get("key"), "summary": st.get("fields", {}).get("summary")}
                for st in fields.get("subtasks", [])
            ],
            "parent": {
                "key": fields.get("parent", {}).get("key"),
                "summary": fields.get("parent", {}).get("fields", {}).get("summary"),
            }
            if fields.get("parent")
            else None,
            "links": [
                {
                    "type": link.get("type", {}).get("name"),
                    "inward": link.get("inwardIssue", {}).get("key"),
                    "outward": link.get("outwardIssue", {}).get("key"),
                }
                for link in fields.get("issuelinks", [])
            ],
            "customFields": self._extract_custom_fields(fields),
            "_exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        return processed

    def _extract_custom_fields(self, fields: dict) -> dict:
        """Extract custom fields (fields starting with 'customfield_')."""
        custom = {}
        for key, value in fields.items():
            if key.startswith("customfield_") and value is not None:
                # Try to extract meaningful value
                if isinstance(value, dict):
                    if "value" in value:
                        custom[key] = value["value"]
                    elif "name" in value:
                        custom[key] = value["name"]
                    else:
                        custom[key] = value
                elif isinstance(value, list) and value:
                    if isinstance(value[0], dict):
                        custom[key] = [
                            v.get("value") or v.get("name") or v for v in value
                        ]
                    else:
                        custom[key] = value
                else:
                    custom[key] = value
        return custom

    def export_issues(self, jql: str, progress: bool = True) -> list[str]:
        """Export all issues matching JQL to JSON files."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if progress:
            print(f"Searching for issues with JQL: {jql}")

        issues = self.search_issues(jql)

        if progress:
            print(f"Found {len(issues)} issues")

        exported_files = []
        for i, issue in enumerate(issues):
            issue_key = issue.get("key")
            if progress:
                print(f"Processing {i + 1}/{len(issues)}: {issue_key}")

            processed = self.process_issue(issue)

            # Save to file
            filename = self.output_dir / f"{issue_key}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(processed, f, indent=2, ensure_ascii=False)

            exported_files.append(str(filename))

        # Create an index file
        index = {
            "exportDate": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "jql": jql,
            "totalIssues": len(issues),
            "issues": [
                {
                    "key": issue.get("key"),
                    "summary": issue.get("fields", {}).get("summary"),
                    "status": issue.get("fields", {}).get("status", {}).get("name"),
                    "file": f"{issue.get('key')}.json",
                }
                for issue in issues
            ],
        }

        index_file = self.output_dir / "_index.json"
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

        if progress:
            print(f"\nExported {len(issues)} issues to {self.output_dir}/")
            print(f"Index file: {index_file}")

        return exported_files

    def export_to_db(self, jql: str, db_path: str = "jira.db", progress: bool = True) -> int:
        """Export all issues matching JQL to SQLite database."""
        if progress:
            print(f"Searching for issues with JQL: {jql}")

        issues = self.search_issues(jql)

        if progress:
            print(f"Found {len(issues)} issues")

        if not issues:
            return 0

        db = JiraDatabase(db_path)

        for i, issue in enumerate(issues):
            issue_key = issue.get("key")
            if progress:
                print(f"Processing {i + 1}/{len(issues)}: {issue_key}")

            processed = self.process_issue(issue)
            db.insert_ticket(processed)

        stats = db.get_stats()
        db.close()

        if progress:
            print(f"\nExported {len(issues)} issues to {db_path}")
            print(f"Database stats:")
            print(f"  Total tickets: {stats['tickets']}")
            print(f"  Total comments: {stats['comments']}")
            print(f"  Total changelog entries: {stats['changelog_entries']}")
            print(f"  Projects: {stats['projects']}")

        return len(issues)


def main():
    parser = argparse.ArgumentParser(
        description="Export JIRA tickets to JSON files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--project",
        "-p",
        help="JIRA project key (e.g., PROJ)",
    )
    parser.add_argument(
        "--projects",
        nargs="+",
        metavar="PROJ",
        help="Multiple JIRA project keys (e.g., --projects PROJ1 PROJ2 PROJ3)",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="Fetch all accessible projects",
    )
    parser.add_argument(
        "--jql",
        "-j",
        help="Custom JQL query (overrides --project and --since)",
    )
    parser.add_argument(
        "--since",
        "-s",
        help="Only export issues created since this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="jira_export",
        help="Output directory (default: jira_export)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress output",
    )
    parser.add_argument(
        "--test",
        "-t",
        action="store_true",
        help="Test connection and show account info",
    )
    parser.add_argument(
        "--list-projects",
        action="store_true",
        help="List all accessible projects",
    )
    parser.add_argument(
        "--optimize",
        nargs="+",
        metavar="DB_FILE",
        help="Optimize existing database(s) - adds indexes and rebuilds FTS",
    )
    parser.add_argument(
        "--db",
        nargs="?",
        const=True,
        metavar="FILE",
        help="Export to SQLite database instead of JSON files. If no filename given with --project, uses PROJECT.db",
    )

    args = parser.parse_args()

    # Initialize scraper
    config = JiraConfig.from_env()
    scraper = JiraScraper(config, output_dir=args.output)

    # Handle --test
    if args.test:
        scraper.test_connection()
        sys.exit(0)

    # Handle --list-projects
    if args.list_projects:
        if not scraper.test_connection():
            sys.exit(1)
        projects = scraper.list_projects()
        if projects:
            print(f"Found {len(projects)} accessible projects:\n")
            for p in projects:
                archived = " (archived)" if p.get("archived") else ""
                print(f"  {p.get('key'):12} {p.get('name')}{archived}")
        else:
            print("No accessible projects found.")
        sys.exit(0)

    # Handle --optimize
    if args.optimize:
        for db_path in args.optimize:
            if not Path(db_path).exists():
                print(f"Database not found: {db_path}")
                continue

            print(f"Optimizing {db_path}...")
            db = JiraDatabase(db_path)

            # Get stats before
            stats = db.get_stats()
            print(f"  Tickets: {stats['tickets']}")

            # Rebuild FTS index
            print("  Rebuilding full-text search index...")
            db.rebuild_fts()

            # Analyze for query optimization
            print("  Analyzing tables...")
            db.conn.execute("ANALYZE")

            # Vacuum to reclaim space
            print("  Vacuuming database...")
            db.conn.execute("VACUUM")

            db.close()
            print(f"  Done! Database optimized.")
            print()

        sys.exit(0)

    # Build list of projects to fetch
    project_list = []

    if args.all_projects:
        # Fetch all accessible projects
        print("Fetching list of all accessible projects...")
        projects = scraper.list_projects()
        project_list = [p.get("key") for p in projects if p.get("key")]
        print(f"Found {len(project_list)} projects: {', '.join(project_list)}")
    elif args.projects:
        project_list = args.projects
    elif args.project:
        project_list = [args.project]
    elif args.jql:
        # Single JQL query mode
        project_list = None
    else:
        parser.error("Either --project, --projects, --all-projects, or --jql is required")

    try:
        if project_list:
            # Multiple projects mode
            total_issues = 0
            for i, project_key in enumerate(project_list):
                print(f"\n{'='*60}")
                print(f"Project {i+1}/{len(project_list)}: {project_key}")
                print('='*60)

                jql = f'project = "{project_key}"'
                if args.since:
                    jql += f" AND created >= '{args.since}'"
                jql += " ORDER BY created DESC"

                if args.db:
                    # Each project gets its own database
                    if args.db is True or len(project_list) > 1:
                        db_path = f"{project_key}.db"
                    else:
                        db_path = args.db
                    count = scraper.export_to_db(jql, db_path=db_path, progress=not args.quiet)
                    total_issues += count
                else:
                    # Each project gets its own output directory
                    scraper.output_dir = Path(args.output) / project_key
                    files = scraper.export_issues(jql, progress=not args.quiet)
                    total_issues += len(files)

            print(f"\n{'='*60}")
            print(f"COMPLETE: Exported {total_issues} total issues from {len(project_list)} projects")
            print('='*60)
        else:
            # Single JQL query mode
            jql = args.jql
            if args.db:
                db_path = args.db if args.db is not True else "jira.db"
                scraper.export_to_db(jql, db_path=db_path, progress=not args.quiet)
            else:
                scraper.export_issues(jql, progress=not args.quiet)

    except requests.HTTPError as e:
        print(f"JIRA API error: {e}")
        if e.response is not None:
            print(f"Response: {e.response.text}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nExport cancelled")
        sys.exit(1)


if __name__ == "__main__":
    main()
