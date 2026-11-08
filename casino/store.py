"""SQLite is authoritative. JSONL is an atomic, repairable projection."""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

def timestamp():
    return datetime.now(ZoneInfo("America/Chicago")).isoformat()

class Store:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "attempts.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY, level INTEGER NOT NULL, prompt TEXT NOT NULL,
            response TEXT, tokens INTEGER, cleared INTEGER, timestamp TEXT NOT NULL,
            status TEXT NOT NULL, result_json TEXT NOT NULL)""")
        self.db.commit()
        # Legacy ledgers enforced UNIQUE(level,prompt). Preserve all rows/IDs
        # while removing that constraint so every explicit attempt can be logged.
        unique_prompt = any(
            index[2] and [c[2] for c in self.db.execute(
                'PRAGMA index_info("' + index[1].replace('"', '""') + '")'
            )] == ["level", "prompt"]
            for index in self.db.execute("PRAGMA index_list(attempts)").fetchall()
        )
        if unique_prompt:
            backup = self.root / "attempts.pre-duplicates.db"
            if not backup.exists():
                with sqlite3.connect(backup) as target:
                    self.db.backup(target)
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("""CREATE TABLE attempts_duplicates_migration (
                    id INTEGER PRIMARY KEY, level INTEGER NOT NULL, prompt TEXT NOT NULL,
                    response TEXT, tokens INTEGER, cleared INTEGER, timestamp TEXT NOT NULL,
                    status TEXT NOT NULL, result_json TEXT NOT NULL)""")
                self.db.execute("INSERT INTO attempts_duplicates_migration SELECT * FROM attempts")
                self.db.execute("DROP TABLE attempts")
                self.db.execute("ALTER TABLE attempts_duplicates_migration RENAME TO attempts")
        self.db.execute("CREATE INDEX IF NOT EXISTS attempts_prompt_lookup ON attempts(level,prompt,id)")
        self.db.commit()
        self.export()

    def lookup(self, level, prompt):
        row = self.db.execute("SELECT result_json FROM attempts WHERE level=? AND prompt=? ORDER BY id DESC LIMIT 1", (level,prompt)).fetchone()
        return json.loads(row[0]) if row else None

    def begin(self, level, prompt):
        result = dict(level=level,prompt=prompt,status="pending",timestamp=timestamp(),
                      response=None,tokens=None,cleared=None)
        cur = self.db.execute("INSERT INTO attempts(level,prompt,timestamp,status,result_json) VALUES(?,?,?,?,?)",
                              (level,prompt,result["timestamp"],"pending",json.dumps(result)))
        result["id"] = cur.lastrowid
        self.db.execute("UPDATE attempts SET result_json=? WHERE id=?", (json.dumps(result),result["id"]))
        self.db.commit()
        self.export()
        return result

    def finish(self, result):
        self.db.execute("UPDATE attempts SET response=?,tokens=?,cleared=?,status=?,result_json=? WHERE id=?",
                        (result.get("response"),result.get("tokens"),result.get("cleared"),result["status"],json.dumps(result,ensure_ascii=False),result["id"]))
        self.db.commit()
        self.export()
        return result

    def list(self, level=None, limit=100):
        if not isinstance(limit,int) or isinstance(limit,bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        query = "SELECT result_json FROM attempts"
        params = []
        if level is not None:
            query += " WHERE level=?"; params.append(level)
        query += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        return [json.loads(row[0]) for row in self.db.execute(query,params)]

    def export(self):
        runs = self.root / "runs"; runs.mkdir(exist_ok=True)
        tmp = runs / "attempts.jsonl.tmp"
        with tmp.open("w") as f:
            for row in self.db.execute("SELECT result_json FROM attempts ORDER BY id"):
                f.write(row[0] + "\n")
            f.flush(); os.fsync(f.fileno())
        tmp.replace(runs / "attempts.jsonl")

    def close(self):
        self.db.close()
