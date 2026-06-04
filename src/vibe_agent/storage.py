import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  published_at TEXT,
  score REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS apify_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  actor_id TEXT NOT NULL DEFAULT '',
  query TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  score REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, url)
);

CREATE TABLE IF NOT EXISTS osint_tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'awesome-osint',
  score REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, url)
);

CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS draft_variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  content TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(draft_id, platform),
  FOREIGN KEY(draft_id) REFERENCES drafts(id)
);

CREATE TABLE IF NOT EXISTS draft_compare_variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  selected INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(draft_id) REFERENCES drafts(id)
);

CREATE TABLE IF NOT EXISTS style_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL DEFAULT 'rule',
  content TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 5,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS draft_research_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  item_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(draft_id) REFERENCES drafts(id),
  FOREIGN KEY(item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS task_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER,
  item_id INTEGER,
  title TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  due_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(draft_id) REFERENCES drafts(id),
  FOREIGN KEY(item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS model_cookbook (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '',
  endpoint TEXT NOT NULL DEFAULT '',
  model_id TEXT NOT NULL DEFAULT '',
  hardware TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'candidate',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS draft_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(draft_id) REFERENCES drafts(id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  objective TEXT NOT NULL DEFAULT '',
  item_id INTEGER,
  draft_id INTEGER,
  summary TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  FOREIGN KEY(item_id) REFERENCES items(id),
  FOREIGN KEY(draft_id) REFERENCES drafts(id)
);

CREATE TABLE IF NOT EXISTS agent_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  risk_class TEXT NOT NULL DEFAULT 'compute_only',
  summary TEXT NOT NULL DEFAULT '',
  observation_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  item_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  status TEXT NOT NULL,
  external_id TEXT,
  external_url TEXT,
  response_json TEXT NOT NULL DEFAULT '{}',
  content TEXT NOT NULL,
  image_path TEXT,
  published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(draft_id) REFERENCES drafts(id),
  FOREIGN KEY(item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS media_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  item_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'image',
  path TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'generated',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(draft_id) REFERENCES drafts(id),
  FOREIGN KEY(item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS publication_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL,
  item_id INTEGER NOT NULL,
  platform TEXT NOT NULL,
  content TEXT NOT NULL,
  image_path TEXT,
  scheduled_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'scheduled',
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(draft_id) REFERENCES drafts(id),
  FOREIGN KEY(item_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blog_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL DEFAULT 'article',
  excerpt TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  cover_path TEXT,
  demo_url TEXT,
  trial_limit INTEGER NOT NULL DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'published',
  source_draft_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  visitor_id TEXT NOT NULL,
  uses_count INTEGER NOT NULL DEFAULT 0,
  last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(post_id, visitor_id),
  FOREIGN KEY(post_id) REFERENCES blog_posts(id)
);

CREATE TABLE IF NOT EXISTS post_reactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  visitor_id TEXT NOT NULL,
  reaction TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(post_id, visitor_id, reaction),
  FOREIGN KEY(post_id) REFERENCES blog_posts(id)
);

CREATE TABLE IF NOT EXISTS post_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'published',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(post_id) REFERENCES blog_posts(id)
);

CREATE TABLE IF NOT EXISTS post_visits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  visitor_id TEXT NOT NULL,
  views_count INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(post_id, visitor_id),
  FOREIGN KEY(post_id) REFERENCES blog_posts(id)
);

CREATE TABLE IF NOT EXISTS growth_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS growth_tests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_name TEXT NOT NULL,
  channel_url TEXT NOT NULL DEFAULT '',
  segment TEXT NOT NULL DEFAULT '',
  placement_type TEXT NOT NULL DEFAULT 'direct',
  cost_rub REAL NOT NULL DEFAULT 0,
  invite_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned',
  notes TEXT NOT NULL DEFAULT '',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Storage:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(publications)").fetchall()
        }
        if "image_path" not in columns:
            conn.execute("ALTER TABLE publications ADD COLUMN image_path TEXT")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS blog_posts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              slug TEXT UNIQUE NOT NULL,
              kind TEXT NOT NULL DEFAULT 'article',
              excerpt TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL,
              cover_path TEXT,
              demo_url TEXT,
              trial_limit INTEGER NOT NULL DEFAULT 5,
              status TEXT NOT NULL DEFAULT 'published',
              source_draft_id INTEGER,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS project_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              post_id INTEGER NOT NULL,
              visitor_id TEXT NOT NULL,
              uses_count INTEGER NOT NULL DEFAULT 0,
              last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(post_id, visitor_id),
              FOREIGN KEY(post_id) REFERENCES blog_posts(id)
            );

            CREATE TABLE IF NOT EXISTS draft_variants (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id INTEGER NOT NULL,
              platform TEXT NOT NULL,
              content TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(draft_id, platform),
              FOREIGN KEY(draft_id) REFERENCES drafts(id)
            );

            CREATE TABLE IF NOT EXISTS draft_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id INTEGER NOT NULL,
              content TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(draft_id) REFERENCES drafts(id)
            );

            CREATE TABLE IF NOT EXISTS draft_compare_variants (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id INTEGER NOT NULL,
              label TEXT NOT NULL DEFAULT '',
              provider TEXT NOT NULL DEFAULT '',
              model TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              selected INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(draft_id) REFERENCES drafts(id)
            );

            CREATE TABLE IF NOT EXISTS style_memory (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL DEFAULT 'rule',
              content TEXT NOT NULL,
              weight INTEGER NOT NULL DEFAULT 5,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS draft_research_reports (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id INTEGER NOT NULL,
              item_id INTEGER NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(draft_id) REFERENCES drafts(id),
              FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS task_notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id INTEGER,
              item_id INTEGER,
              title TEXT NOT NULL,
              content TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'open',
              due_at TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(draft_id) REFERENCES drafts(id),
              FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS model_cookbook (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              provider TEXT NOT NULL DEFAULT '',
              role TEXT NOT NULL DEFAULT '',
              endpoint TEXT NOT NULL DEFAULT '',
              model_id TEXT NOT NULL DEFAULT '',
              hardware TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'candidate',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'running',
              objective TEXT NOT NULL DEFAULT '',
              item_id INTEGER,
              draft_id INTEGER,
              summary TEXT NOT NULL DEFAULT '',
              error TEXT NOT NULL DEFAULT '',
              started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              finished_at TEXT,
              FOREIGN KEY(item_id) REFERENCES items(id),
              FOREIGN KEY(draft_id) REFERENCES drafts(id)
            );

            CREATE TABLE IF NOT EXISTS agent_steps (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id INTEGER NOT NULL,
              role TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'running',
              risk_class TEXT NOT NULL DEFAULT 'compute_only',
              summary TEXT NOT NULL DEFAULT '',
              observation_json TEXT NOT NULL DEFAULT '{}',
              started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              finished_at TEXT,
              FOREIGN KEY(run_id) REFERENCES agent_runs(id)
            );

            CREATE TABLE IF NOT EXISTS post_reactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              post_id INTEGER NOT NULL,
              visitor_id TEXT NOT NULL,
              reaction TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(post_id, visitor_id, reaction),
              FOREIGN KEY(post_id) REFERENCES blog_posts(id)
            );

            CREATE TABLE IF NOT EXISTS post_comments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              post_id INTEGER NOT NULL,
              author TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'published',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(post_id) REFERENCES blog_posts(id)
            );

            CREATE TABLE IF NOT EXISTS post_visits (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              post_id INTEGER NOT NULL,
              visitor_id TEXT NOT NULL,
              views_count INTEGER NOT NULL DEFAULT 0,
              last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(post_id, visitor_id),
              FOREIGN KEY(post_id) REFERENCES blog_posts(id)
            );

            CREATE TABLE IF NOT EXISTS growth_links (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS growth_tests (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              channel_name TEXT NOT NULL,
              channel_url TEXT NOT NULL DEFAULT '',
              segment TEXT NOT NULL DEFAULT '',
              placement_type TEXT NOT NULL DEFAULT 'direct',
              cost_rub REAL NOT NULL DEFAULT 0,
              invite_url TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'planned',
              notes TEXT NOT NULL DEFAULT '',
              metrics_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS osint_tools (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              category TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT 'awesome-osint',
              score REAL NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(source, url)
            );
            """
        )

    def upsert_item(self, item: dict[str, Any]) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO items
                  (url, title, summary, source, published_at, score, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["url"],
                    item["title"],
                    item.get("summary", ""),
                    item["source"],
                    item.get("published_at"),
                    item.get("score", 0),
                    item.get("status", "new"),
                ),
            )
            return cur.rowcount > 0

    def update_item_score(self, item_id: int, score: float) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE items SET score = ? WHERE id = ?", (score, item_id))

    def list_items(self, limit: int = 30, query: str | None = None) -> list[dict[str, Any]]:
        where = ""
        args: list[Any] = []
        if query:
            where = "WHERE title LIKE ? OR summary LIKE ? OR source LIKE ?"
            like = f"%{query}%"
            args.extend([like, like, like])
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM items {where} ORDER BY score DESC, created_at DESC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_editorial_candidates(self, limit: int = 40) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT items.*
                FROM items
                WHERE items.status != 'error'
                  AND items.url NOT LIKE 'error://%'
                  AND NOT EXISTS (
                    SELECT 1 FROM drafts
                    WHERE drafts.item_id = items.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM publications
                    WHERE publications.item_id = items.id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM agent_runs
                    WHERE agent_runs.kind = 'editorial'
                      AND agent_runs.item_id = items.id
                      AND agent_runs.status IN ('success', 'warning', 'running')
                  )
                ORDER BY items.score DESC, items.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_items_by_source_prefix(
        self,
        source_prefix: str,
        limit: int = 120,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        where = "WHERE source LIKE ?"
        args: list[Any] = [f"{source_prefix}%"]
        if query:
            where += " AND (title LIKE ? OR summary LIKE ? OR source LIKE ?)"
            like = f"%{query}%"
            args.extend([like, like, like])
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM items {where} ORDER BY score DESC, created_at DESC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_apify_item(self, item: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO apify_items
                  (url, title, summary, source, actor_id, query, published_at, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, url) DO UPDATE SET
                  title = excluded.title,
                  summary = excluded.summary,
                  actor_id = excluded.actor_id,
                  query = excluded.query,
                  published_at = excluded.published_at,
                  score = excluded.score,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item["url"],
                    item["title"],
                    item.get("summary", ""),
                    item["source"],
                    item.get("actor_id", ""),
                    item.get("query", ""),
                    item.get("published_at"),
                    item.get("score", 0),
                ),
            )

    def list_apify_items(self, limit: int = 120, query: str | None = None) -> list[dict[str, Any]]:
        where = ""
        args: list[Any] = []
        if query:
            where = """
            WHERE apify_items.title LIKE ?
               OR apify_items.summary LIKE ?
               OR apify_items.source LIKE ?
               OR apify_items.actor_id LIKE ?
            """
            like = f"%{query}%"
            args.extend([like, like, like, like])
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT apify_items.*, items.id AS item_id
                FROM apify_items
                LEFT JOIN items ON items.url = apify_items.url
                {where}
                ORDER BY apify_items.score DESC, apify_items.updated_at DESC
                LIMIT ?
                """,
                tuple(args),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_osint_tool(self, tool: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO osint_tools
                  (name, url, description, category, source, score)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, url) DO UPDATE SET
                  name = excluded.name,
                  description = excluded.description,
                  category = excluded.category,
                  score = excluded.score,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    tool["name"],
                    tool["url"],
                    tool.get("description", ""),
                    tool.get("category", ""),
                    tool.get("source", "awesome-osint"),
                    tool.get("score", 0),
                ),
            )

    def clear_osint_catalog(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM osint_tools")
            conn.execute(
                """
                DELETE FROM items
                WHERE source LIKE 'OSINT:%'
                  AND id NOT IN (SELECT item_id FROM drafts)
                  AND id NOT IN (SELECT item_id FROM publications)
                """
            )

    def list_osint_tools(
        self,
        limit: int = 120,
        query: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        args: list[Any] = []
        if query:
            where_parts.append(
                """
                (osint_tools.name LIKE ?
                 OR osint_tools.description LIKE ?
                 OR osint_tools.category LIKE ?
                 OR osint_tools.url LIKE ?)
                """
            )
            like = f"%{query}%"
            args.extend([like, like, like, like])
        if category:
            where_parts.append("osint_tools.category = ?")
            args.append(category)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT osint_tools.*, items.id AS item_id
                FROM osint_tools
                LEFT JOIN items ON items.url = osint_tools.url
                {where}
                ORDER BY osint_tools.score DESC, osint_tools.updated_at DESC
                LIMIT ?
                """,
                tuple(args),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_osint_categories(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS count
                FROM osint_tools
                WHERE category != ''
                GROUP BY category
                ORDER BY count DESC, category ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def save_draft(self, item_id: int, platform: str, content: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO drafts (item_id, platform, content) VALUES (?, ?, ?)",
                (item_id, platform, content),
            )
            draft_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO draft_history (draft_id, content, note) VALUES (?, ?, ?)",
                (draft_id, content, "Исходный черновик"),
            )
            return draft_id

    def get_draft(self, draft_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def update_draft_status(self, draft_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE drafts SET status = ? WHERE id = ?", (status, draft_id))

    def update_draft_content(self, draft_id: int, content: str, status: str = "draft") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE drafts SET content = ?, status = ? WHERE id = ?",
                (content, status, draft_id),
            )

    def save_draft_revision(self, draft_id: int, content: str, note: str = "") -> int | None:
        clean_content = (content or "").strip()
        if not clean_content:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT content FROM draft_history
                WHERE draft_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (draft_id,),
            ).fetchone()
            if row and row["content"] == clean_content:
                return None
            cur = conn.execute(
                """
                INSERT INTO draft_history (draft_id, content, note)
                VALUES (?, ?, ?)
                """,
                (draft_id, clean_content, note),
            )
            return int(cur.lastrowid)

    def list_draft_history(self, draft_id: int, limit: int = 12) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM draft_history
                WHERE draft_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (draft_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_draft_revision(self, revision_id: int, draft_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM draft_history WHERE id = ? AND draft_id = ?",
                (revision_id, draft_id),
            ).fetchone()
        return dict(row) if row else None

    def list_drafts(self, item_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM drafts"
        args: tuple[Any, ...] = ()
        if item_id:
            query += " WHERE item_id = ?"
            args = (item_id,)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [dict(row) for row in rows]

    def upsert_draft_variant(self, draft_id: int, platform: str, content: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO draft_variants (draft_id, platform, content)
                VALUES (?, ?, ?)
                ON CONFLICT(draft_id, platform) DO UPDATE SET
                  content = excluded.content,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (draft_id, platform, content),
            )

    def list_draft_variants(self, draft_id: int) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT platform, content FROM draft_variants WHERE draft_id = ?",
                (draft_id,),
            ).fetchall()
        return {row["platform"]: row["content"] for row in rows}

    def add_draft_compare_variant(
        self,
        draft_id: int,
        label: str,
        provider: str,
        model: str,
        content: str,
        note: str = "",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO draft_compare_variants
                  (draft_id, label, provider, model, content, note)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (draft_id, label, provider, model, content, note),
            )
            return int(cur.lastrowid)

    def list_draft_compare_variants(self, draft_id: int, limit: int = 12) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM draft_compare_variants
                WHERE draft_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (draft_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_draft_compare_variant(self, draft_id: int, variant_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM draft_compare_variants
                WHERE draft_id = ? AND id = ?
                """,
                (draft_id, variant_id),
            ).fetchone()
        return dict(row) if row else None

    def mark_draft_compare_variant_selected(self, draft_id: int, variant_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE draft_compare_variants SET selected = 0 WHERE draft_id = ?",
                (draft_id,),
            )
            conn.execute(
                "UPDATE draft_compare_variants SET selected = 1 WHERE draft_id = ? AND id = ?",
                (draft_id, variant_id),
            )

    def add_style_memory(self, kind: str, content: str, weight: int = 5) -> int | None:
        clean_content = (content or "").strip()
        if not clean_content:
            return None
        clean_kind = kind if kind in {"rule", "ban", "phrase", "example"} else "rule"
        clean_weight = max(1, min(int(weight or 5), 10))
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO style_memory (kind, content, weight)
                VALUES (?, ?, ?)
                """,
                (clean_kind, clean_content, clean_weight),
            )
            return int(cur.lastrowid)

    def list_style_memory(self, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM style_memory
                ORDER BY weight DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_style_memory(self, memory_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM style_memory WHERE id = ?", (memory_id,))

    def style_memory_text(self, limit: int = 40) -> str:
        rows = self.list_style_memory(limit=limit)
        if not rows:
            return ""
        labels = {
            "rule": "Правило",
            "ban": "Запрет",
            "phrase": "Удачная фраза",
            "example": "Пример",
        }
        lines = ["\n\nПамять стиля:"]
        for row in rows:
            label = labels.get(row["kind"], "Правило")
            lines.append(f"- {label}: {row['content']}")
        return "\n".join(lines)

    def add_research_report(self, draft_id: int, item_id: int, title: str, content: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO draft_research_reports (draft_id, item_id, title, content)
                VALUES (?, ?, ?, ?)
                """,
                (draft_id, item_id, title, content),
            )
            return int(cur.lastrowid)

    def list_research_reports(self, draft_id: int, limit: int = 6) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM draft_research_reports
                WHERE draft_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (draft_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_task_note(
        self,
        title: str,
        content: str = "",
        draft_id: int | None = None,
        item_id: int | None = None,
        due_at: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO task_notes (draft_id, item_id, title, content, due_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (draft_id, item_id, title.strip(), content.strip(), due_at or None),
            )
            return int(cur.lastrowid)

    def list_task_notes(
        self,
        draft_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        args: list[Any] = []
        if draft_id is not None:
            where_parts.append("task_notes.draft_id = ?")
            args.append(draft_id)
        if status:
            where_parts.append("task_notes.status = ?")
            args.append(status)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT task_notes.*, drafts.platform AS draft_platform, items.title AS item_title
                FROM task_notes
                LEFT JOIN drafts ON drafts.id = task_notes.draft_id
                LEFT JOIN items ON items.id = COALESCE(task_notes.item_id, drafts.item_id)
                {where}
                ORDER BY
                  CASE task_notes.status WHEN 'open' THEN 0 WHEN 'done' THEN 2 ELSE 1 END,
                  task_notes.due_at IS NULL,
                  task_notes.due_at ASC,
                  task_notes.id DESC
                LIMIT ?
                """,
                tuple(args),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_task_note_status(self, note_id: int, status: str) -> None:
        clean_status = status if status in {"open", "waiting", "done"} else "open"
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE task_notes
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (clean_status, note_id),
            )

    def delete_task_note(self, note_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM task_notes WHERE id = ?", (note_id,))

    def add_model_cookbook_entry(
        self,
        name: str,
        provider: str = "",
        role: str = "",
        endpoint: str = "",
        model_id: str = "",
        hardware: str = "",
        notes: str = "",
        status: str = "candidate",
    ) -> int:
        clean_status = status if status in {"active", "candidate", "fallback", "paused"} else "candidate"
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO model_cookbook
                  (name, provider, role, endpoint, model_id, hardware, notes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    provider.strip(),
                    role.strip(),
                    endpoint.strip(),
                    model_id.strip(),
                    hardware.strip(),
                    notes.strip(),
                    clean_status,
                ),
            )
            return int(cur.lastrowid)

    def list_model_cookbook_entries(self, limit: int = 120) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM model_cookbook
                ORDER BY
                  CASE status WHEN 'active' THEN 0 WHEN 'fallback' THEN 1 WHEN 'candidate' THEN 2 ELSE 3 END,
                  id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_model_cookbook_entry(self, entry_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM model_cookbook WHERE id = ?", (entry_id,))

    def create_agent_run(
        self,
        kind: str,
        objective: str,
        item_id: int | None = None,
        draft_id: int | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO agent_runs (kind, objective, item_id, draft_id)
                VALUES (?, ?, ?, ?)
                """,
                (kind, objective, item_id, draft_id),
            )
            return int(cur.lastrowid)

    def finish_agent_run(
        self,
        run_id: int,
        status: str,
        summary: str = "",
        error: str = "",
        item_id: int | None = None,
        draft_id: int | None = None,
    ) -> None:
        assignments = ["status = ?", "summary = ?", "error = ?", "finished_at = CURRENT_TIMESTAMP"]
        values: list[Any] = [status, summary, error]
        if item_id is not None:
            assignments.append("item_id = ?")
            values.append(item_id)
        if draft_id is not None:
            assignments.append("draft_id = ?")
            values.append(draft_id)
        values.append(run_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE agent_runs SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )

    def create_agent_step(
        self,
        run_id: int,
        role: str,
        risk_class: str,
        summary: str = "",
        status: str = "running",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO agent_steps (run_id, role, risk_class, summary, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, role, risk_class, summary, status),
            )
            return int(cur.lastrowid)

    def finish_agent_step(
        self,
        step_id: int,
        status: str,
        summary: str,
        observation: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_steps
                SET status = ?, summary = ?, observation_json = ?, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, summary, json.dumps(observation or {}, ensure_ascii=False), step_id),
            )

    def list_agent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_runs.*, items.title AS item_title
                FROM agent_runs
                LEFT JOIN items ON items.id = agent_runs.item_id
                ORDER BY agent_runs.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_running_agent_run(self, kind: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT agent_runs.*, items.title AS item_title
                FROM agent_runs
                LEFT JOIN items ON items.id = agent_runs.item_id
                WHERE agent_runs.kind = ? AND agent_runs.status = 'running'
                ORDER BY agent_runs.started_at DESC
                LIMIT 1
                """,
                (kind,),
            ).fetchone()
        return dict(row) if row else None

    def interrupt_running_agent_runs(self, kind: str, summary: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = 'interrupted',
                    summary = ?,
                    error = 'service restarted before the background run finished',
                    finished_at = CURRENT_TIMESTAMP
                WHERE kind = ? AND status = 'running'
                """,
                (summary, kind),
            )
            conn.execute(
                """
                UPDATE agent_steps
                SET status = 'interrupted',
                    summary = summary || ' Прервано перезапуском сервиса.',
                    finished_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                  AND run_id IN (SELECT id FROM agent_runs WHERE kind = ? AND status = 'interrupted')
                """,
                (kind,),
            )

    def get_agent_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT agent_runs.*, items.title AS item_title
                FROM agent_runs
                LEFT JOIN items ON items.id = agent_runs.item_id
                WHERE agent_runs.id = ?
                """,
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_agent_steps(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_steps
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_publication(
        self,
        draft_id: int,
        item_id: int,
        platform: str,
        status: str,
        content: str,
        response: dict[str, Any] | None = None,
        image_path: str | None = None,
    ) -> int:
        response = response or {}
        external_id = extract_external_id(platform, response)
        external_url = extract_external_url(platform, response)
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO publications
                  (draft_id, item_id, platform, status, external_id, external_url, response_json, content, image_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    item_id,
                    platform,
                    status,
                    external_id,
                    external_url,
                    json.dumps(response, ensure_ascii=False),
                    content,
                    image_path,
                ),
            )
            return int(cur.lastrowid)

    def list_publications(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  publications.*,
                  items.title AS item_title,
                  items.url AS source_url
                FROM publications
                JOIN items ON items.id = publications.item_id
                ORDER BY publications.published_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_media_asset(
        self,
        draft_id: int,
        item_id: int,
        path: str,
        prompt: str = "",
        source: str = "generated",
        kind: str = "image",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO media_assets (draft_id, item_id, kind, path, prompt, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (draft_id, item_id, kind, path, prompt, source),
            )
            return int(cur.lastrowid)

    def get_latest_media_asset(self, draft_id: int, kind: str = "image") -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM media_assets
                WHERE draft_id = ? AND kind = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (draft_id, kind),
            ).fetchone()
        return dict(row) if row else None

    def list_media_assets(
        self, draft_id: int | None = None, kind: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        args: list[Any] = []
        if draft_id is not None:
            where.append("media_assets.draft_id = ?")
            args.append(draft_id)
        if kind:
            where.append("media_assets.kind = ?")
            args.append(kind)
        args.append(limit)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT media_assets.*, items.title AS item_title
                FROM media_assets
                LEFT JOIN items ON items.id = media_assets.item_id
                {clause}
                ORDER BY media_assets.created_at DESC
                LIMIT ?
                """,
                tuple(args),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_media_asset(self, asset_id: int, draft_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM media_assets WHERE id = ? AND draft_id = ?",
                (asset_id, draft_id),
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM media_assets WHERE id = ? AND draft_id = ?", (asset_id, draft_id))
        return dict(row)

    def mark_media_asset_current(self, asset_id: int, draft_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM media_assets WHERE id = ? AND draft_id = ?",
                (asset_id, draft_id),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE media_assets SET created_at = CURRENT_TIMESTAMP WHERE id = ? AND draft_id = ?",
                (asset_id, draft_id),
            )
        return True

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def get_settings_map(self, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT key, value FROM app_settings WHERE key IN ({placeholders})",
                tuple(keys),
            ).fetchall()
        return {row["key"]: row["value"] for row in rows}

    def schedule_publication(
        self,
        draft_id: int,
        item_id: int,
        platform: str,
        content: str,
        scheduled_at: str,
        image_path: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO publication_queue
                  (draft_id, item_id, platform, content, image_path, scheduled_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (draft_id, item_id, platform, content, image_path, scheduled_at),
            )
            return int(cur.lastrowid)

    def list_publication_queue(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  publication_queue.*,
                  items.title AS item_title,
                  items.url AS source_url
                FROM publication_queue
                JOIN items ON items.id = publication_queue.item_id
                ORDER BY publication_queue.scheduled_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_due_publications(self, now_iso: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM publication_queue
                WHERE status = 'scheduled' AND scheduled_at <= ?
                ORDER BY scheduled_at ASC
                """,
                (now_iso,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_queue_status(self, queue_id: int, status: str, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE publication_queue
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, error, queue_id),
            )

    def create_blog_post(
        self,
        title: str,
        slug: str,
        kind: str,
        excerpt: str,
        content: str,
        cover_path: str | None = None,
        demo_url: str | None = None,
        trial_limit: int = 5,
        source_draft_id: int | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO blog_posts
                  (title, slug, kind, excerpt, content, cover_path, demo_url, trial_limit, source_draft_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    slug,
                    kind,
                    excerpt,
                    content,
                    cover_path,
                    demo_url,
                    trial_limit,
                    source_draft_id,
                ),
            )
            return int(cur.lastrowid)

    def list_blog_posts(self, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where = "WHERE status = 'published'"
        args: list[Any] = []
        if kind:
            where += " AND kind = ?"
            args.append(kind)
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM blog_posts {where} ORDER BY created_at DESC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_blog_posts_admin(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM blog_posts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_blog_post(self, post_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM blog_posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None

    def get_blog_post_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM blog_posts WHERE slug = ? AND status = 'published'",
                (slug,),
            ).fetchone()
        return dict(row) if row else None

    def blog_slug_exists(self, slug: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM blog_posts WHERE slug = ?", (slug,)).fetchone()
        return bool(row)

    def blog_slug_exists_for_other_post(self, slug: str, post_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM blog_posts WHERE slug = ? AND id != ?",
                (slug, post_id),
            ).fetchone()
        return bool(row)

    def update_blog_post(
        self,
        post_id: int,
        title: str,
        slug: str,
        kind: str,
        excerpt: str,
        content: str,
        cover_path: str | None,
        demo_url: str | None,
        trial_limit: int,
        status: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE blog_posts
                SET title = ?,
                    slug = ?,
                    kind = ?,
                    excerpt = ?,
                    content = ?,
                    cover_path = ?,
                    demo_url = ?,
                    trial_limit = ?,
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    title,
                    slug,
                    kind,
                    excerpt,
                    content,
                    cover_path,
                    demo_url,
                    trial_limit,
                    status,
                    post_id,
                ),
            )

    def update_blog_post_cover(self, post_id: int, cover_path: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE blog_posts
                SET cover_path = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (cover_path, post_id),
            )

    def delete_blog_post(self, post_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM post_comments WHERE post_id = ?", (post_id,))
            conn.execute("DELETE FROM post_reactions WHERE post_id = ?", (post_id,))
            conn.execute("DELETE FROM post_visits WHERE post_id = ?", (post_id,))
            conn.execute("DELETE FROM project_usage WHERE post_id = ?", (post_id,))
            conn.execute("DELETE FROM blog_posts WHERE id = ?", (post_id,))

    def get_project_usage(self, post_id: int, visitor_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT uses_count FROM project_usage WHERE post_id = ? AND visitor_id = ?",
                (post_id, visitor_id),
            ).fetchone()
        return int(row["uses_count"]) if row else 0

    def record_project_use(self, post_id: int, visitor_id: str) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_usage (post_id, visitor_id, uses_count)
                VALUES (?, ?, 1)
                ON CONFLICT(post_id, visitor_id) DO UPDATE SET
                  uses_count = uses_count + 1,
                  last_used_at = CURRENT_TIMESTAMP
                """,
                (post_id, visitor_id),
            )
            row = conn.execute(
                "SELECT uses_count FROM project_usage WHERE post_id = ? AND visitor_id = ?",
                (post_id, visitor_id),
            ).fetchone()
        return int(row["uses_count"]) if row else 0

    def record_post_view(self, post_id: int, visitor_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO post_visits (post_id, visitor_id, views_count)
                VALUES (?, ?, 1)
                ON CONFLICT(post_id, visitor_id) DO UPDATE SET
                  views_count = views_count + 1,
                  last_seen_at = CURRENT_TIMESTAMP
                """,
                (post_id, visitor_id),
            )

    def post_view_stats(self, post_id: int) -> dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                  COALESCE(SUM(views_count), 0) AS views,
                  COUNT(*) AS visitors
                FROM post_visits
                WHERE post_id = ?
                """,
                (post_id,),
            ).fetchone()
        return {"views": int(row["views"] or 0), "visitors": int(row["visitors"] or 0)}

    def toggle_post_reaction(self, post_id: int, visitor_id: str, reaction: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM post_reactions
                WHERE post_id = ? AND visitor_id = ? AND reaction = ?
                """,
                (post_id, visitor_id, reaction),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM post_reactions WHERE id = ?", (row["id"],))
                return False
            conn.execute(
                """
                INSERT INTO post_reactions (post_id, visitor_id, reaction)
                VALUES (?, ?, ?)
                """,
                (post_id, visitor_id, reaction),
            )
        return True

    def post_reaction_stats(self, post_id: int, visitor_id: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT reaction, COUNT(*) AS count
                FROM post_reactions
                WHERE post_id = ?
                GROUP BY reaction
                """,
                (post_id,),
            ).fetchall()
            selected_rows = []
            if visitor_id:
                selected_rows = conn.execute(
                    """
                    SELECT reaction FROM post_reactions
                    WHERE post_id = ? AND visitor_id = ?
                    """,
                    (post_id, visitor_id),
                ).fetchall()
        return {
            "counts": {row["reaction"]: int(row["count"]) for row in rows},
            "selected": {row["reaction"] for row in selected_rows},
        }

    def add_post_comment(self, post_id: int, author: str, content: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO post_comments (post_id, author, content)
                VALUES (?, ?, ?)
                """,
                (post_id, author, content),
            )
            return int(cur.lastrowid)

    def list_post_comments(self, post_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM post_comments
                WHERE post_id = ? AND status = 'published'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (post_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_growth_link(self, name: str, url: str, source: str = "", notes: str = "") -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO growth_links (name, url, source, notes)
                VALUES (?, ?, ?, ?)
                """,
                (name, url, source, notes),
            )
            return int(cur.lastrowid)

    def list_growth_links(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM growth_links ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_growth_link(self, link_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM growth_links WHERE id = ?", (link_id,))

    def add_growth_test(
        self,
        channel_name: str,
        channel_url: str = "",
        segment: str = "",
        placement_type: str = "direct",
        cost_rub: float = 0,
        invite_url: str = "",
        status: str = "planned",
        notes: str = "",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO growth_tests
                  (channel_name, channel_url, segment, placement_type, cost_rub, invite_url, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_name,
                    channel_url,
                    segment,
                    placement_type,
                    cost_rub,
                    invite_url,
                    status,
                    notes,
                ),
            )
            return int(cur.lastrowid)

    def list_growth_tests(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM growth_tests ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_growth_test(self, test_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM growth_tests WHERE id = ?", (test_id,))


def extract_external_id(platform: str, response: dict[str, Any]) -> str | None:
    if platform == "telegram":
        if response.get("results"):
            response = response["results"][0]
        message_id = response.get("result", {}).get("message_id")
        return str(message_id) if message_id else None
    if platform == "vk":
        post_id = response.get("response", {}).get("post_id")
        return str(post_id) if post_id else None
    return None


def extract_external_url(platform: str, response: dict[str, Any]) -> str | None:
    if platform == "telegram":
        if response.get("results"):
            response = response["results"][0]
        chat = response.get("result", {}).get("chat", {})
        username = chat.get("username")
        message_id = response.get("result", {}).get("message_id")
        if username and message_id:
            return f"https://t.me/{username}/{message_id}"
    return None
