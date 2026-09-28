"""SQLite is the authority. All selectors include a trusted workspace predicate."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import CopilotError
from .schema import Filter, Plan, digest

DDL = """
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL,
  statuses TEXT NOT NULL DEFAULT '["open","won","lost","abandoned"]'
);
CREATE TABLE IF NOT EXISTS stages (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
  name TEXT NOT NULL, position INTEGER NOT NULL,
  PRIMARY KEY (workspace_id, id), UNIQUE (workspace_id, position)
);
CREATE TABLE IF NOT EXISTS owners (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
  name TEXT NOT NULL, PRIMARY KEY (workspace_id, id)
);
CREATE TABLE IF NOT EXISTS opportunities (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
  name TEXT NOT NULL, value_minor INTEGER NOT NULL CHECK(value_minor >= 0),
  status TEXT NOT NULL CHECK(status IN ('open','won','lost','abandoned')),
  owner_id TEXT NOT NULL, stage_id TEXT NOT NULL,
  stage_entered_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, owner_id) REFERENCES owners(workspace_id,id),
  FOREIGN KEY (workspace_id, stage_id) REFERENCES stages(workspace_id,id),
  CHECK(created_at <= stage_entered_at AND stage_entered_at <= updated_at)
);
CREATE INDEX IF NOT EXISTS opportunities_selection
  ON opportunities(workspace_id, stage_id, owner_id, status);
CREATE INDEX IF NOT EXISTS opportunities_created
  ON opportunities(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS opportunities_entered
  ON opportunities(workspace_id, stage_entered_at);
CREATE TABLE IF NOT EXISTS operations (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
  revision INTEGER NOT NULL, state TEXT NOT NULL, instruction TEXT NOT NULL,
  intent_json TEXT, questions_json TEXT, expires_at TEXT NOT NULL,
  interpretation_time TEXT NOT NULL, catalog_hash TEXT NOT NULL,
  PRIMARY KEY(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS plans (
  workspace_id TEXT NOT NULL, id TEXT NOT NULL, operation_id TEXT NOT NULL,
  revision INTEGER NOT NULL, plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL, catalog_hash TEXT NOT NULL,
  match_count INTEGER NOT NULL, total_value_minor INTEGER NOT NULL,
  risk TEXT NOT NULL, token_hash TEXT NOT NULL, state TEXT NOT NULL,
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL, job_id TEXT, preview_json TEXT NOT NULL,
  PRIMARY KEY(workspace_id,id),
  FOREIGN KEY(workspace_id,operation_id) REFERENCES operations(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS challenges (
  workspace_id TEXT NOT NULL, plan_id TEXT NOT NULL, token_hash TEXT NOT NULL,
  phrase TEXT NOT NULL, expires_at TEXT NOT NULL,
  PRIMARY KEY(workspace_id,plan_id),
  FOREIGN KEY(workspace_id,plan_id) REFERENCES plans(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS jobs (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id), id TEXT NOT NULL,
  plan_id TEXT NOT NULL, moved_count INTEGER NOT NULL, completed_at TEXT NOT NULL,
  PRIMARY KEY(workspace_id,id), UNIQUE(workspace_id,plan_id),
  FOREIGN KEY(workspace_id,plan_id) REFERENCES plans(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS audit (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
  event TEXT NOT NULL, object_id TEXT NOT NULL, at TEXT NOT NULL, details TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), isolation_level=None, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(DDL)

    def close(self):
        self.connection.close()

    def clone(self) -> "Store":
        other = Store()
        self.connection.backup(other.connection)
        return other

    @contextmanager
    def transaction(self):
        # The snapshot check and stage write are serialized with every writer.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def workspace(self, workspace_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise CopilotError("workspace_not_found", "This workspace does not exist.")
        result = dict(row)
        result["statuses"] = json.loads(result["statuses"])
        return result

    def catalog(self, workspace_id: str) -> dict:
        workspace = self.workspace(workspace_id)
        return {
            "workspace": workspace,
            "stages": [
                dict(r)
                for r in self.connection.execute(
                    "SELECT id,name,position FROM stages WHERE workspace_id = ? ORDER BY position",
                    (workspace_id,),
                )
            ],
            "owners": [
                dict(r)
                for r in self.connection.execute(
                    "SELECT id,name FROM owners WHERE workspace_id = ? ORDER BY id", (workspace_id,)
                )
            ],
        }

    def validate_entities(self, plan: Plan):
        workspace = self.workspace(plan.workspace_id)
        if plan.filter.status and plan.filter.status not in workspace["statuses"]:
            raise CopilotError("unknown_status", "This status is not enabled in this workspace.")
        for table, entity in (
            ("stages", plan.target_stage_id),
            ("stages", plan.filter.stage_id),
            ("owners", plan.filter.owner_id),
        ):
            if (
                entity is not None
                and self.connection.execute(
                    f"SELECT 1 FROM {table} WHERE workspace_id = ? AND id = ?",
                    (plan.workspace_id, entity),
                ).fetchone()
                is None
            ):
                raise CopilotError("entity_not_found", "An entity is not in this workspace.")

    @staticmethod
    def compile_filter(workspace_id: str, filters: Filter) -> tuple[str, list]:
        # Field names come only from this closed mapping; every value is bound.
        clauses, args = ["workspace_id = ?"], [workspace_id]
        for field in ("stage_id", "owner_id", "status"):
            value = getattr(filters, field)
            if value is not None:
                clauses.append(f"{field} = ?")
                args.append(value)
        for field, operator in (("value_min", ">="), ("value_max", "<=")):
            value = getattr(filters, field)
            if value is not None:
                clauses.append(f"value_minor {operator} ?")
                args.append(value)
        if filters.date:
            for bound, operator in ((filters.date.gte, ">="), (filters.date.lt, "<")):
                if bound is not None:
                    clauses.append(f"{filters.date.field} {operator} ?")
                    args.append(bound)
        return " AND ".join(clauses), args

    def matching(self, workspace_id: str, filters: Filter, limit: int | None = None) -> list[dict]:
        self.workspace(workspace_id)
        where, args = self.compile_filter(workspace_id, filters)
        suffix = ""
        if limit is not None:
            suffix = " LIMIT ?"
            args.append(limit)
        return [
            dict(r)
            for r in self.connection.execute(
                f"SELECT * FROM opportunities WHERE {where} ORDER BY id{suffix}", args
            )
        ]

    def aggregate(self, workspace_id: str, filters: Filter, target_stage_id: str) -> dict:
        self.workspace(workspace_id)
        where, args = self.compile_filter(workspace_id, filters)
        row = self.connection.execute(
            "SELECT count(*) AS count, coalesce(sum(value_minor),0) AS total, "
            "coalesce(sum(CASE WHEN stage_id != ? THEN 1 ELSE 0 END),0) AS movable "
            f"FROM opportunities WHERE {where}",
            [target_stage_id, *args],
        ).fetchone()
        return dict(row)

    def count(self, workspace_id: str, filters: Filter) -> int:
        self.workspace(workspace_id)
        where, args = self.compile_filter(workspace_id, filters)
        return self.connection.execute(
            f"SELECT count(*) FROM opportunities WHERE {where}", args
        ).fetchone()[0]

    def sum(self, workspace_id: str, filters: Filter) -> int:
        self.workspace(workspace_id)
        where, args = self.compile_filter(workspace_id, filters)
        return self.connection.execute(
            f"SELECT coalesce(sum(value_minor), 0) FROM opportunities WHERE {where}", args
        ).fetchone()[0]

    @staticmethod
    def snapshot(rows: list[dict]) -> str:
        # Full ordered rows detect same-count replacement and any visible mutation,
        # including names, versions, exact values and eligibility fields.
        return digest(rows)
