import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
DATABASE_PATH = DATA_DIR / "novel2script.db"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectStore:
    def __init__(self, database_path=DATABASE_PATH, projects_dir=PROJECTS_DIR):
        self.database_path = Path(database_path)
        self.projects_dir = Path(projects_dir)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_filename TEXT,
                    source_char_count INTEGER NOT NULL DEFAULT 0,
                    chapter_count INTEGER NOT NULL DEFAULT 0,
                    character_count INTEGER NOT NULL DEFAULT 0,
                    location_count INTEGER NOT NULL DEFAULT 0,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    scene_count INTEGER NOT NULL DEFAULT 0,
                    parse_mode TEXT,
                    parse_warning TEXT,
                    error_message TEXT,
                    current_step TEXT NOT NULL DEFAULT 'queued',
                    progress INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    repair_count INTEGER NOT NULL DEFAULT 0,
                    project_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(projects)").fetchall()
            }
            migrations = {
                "current_step": "TEXT NOT NULL DEFAULT 'queued'",
                "progress": "INTEGER NOT NULL DEFAULT 0",
                "started_at": "TEXT",
                "finished_at": "TEXT",
                "repair_count": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in migrations.items():
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE projects ADD COLUMN {column} {definition}"
                    )

    def create_project(self, project_id, title, source_text, source_filename=None):
        project_dir = self.project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        self._write_text(project_dir / "source.txt", source_text)
        now = utc_now()
        try:
            stored_project_path = str(project_dir.relative_to(BASE_DIR))
        except ValueError:
            stored_project_path = str(project_dir)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, title, status, source_filename, source_char_count,
                    current_step, progress, started_at,
                    project_path, created_at, updated_at
                ) VALUES (?, ?, 'processing', ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    title or "未命名小说",
                    source_filename,
                    len(source_text),
                    now,
                    stored_project_path,
                    now,
                    now,
                ),
            )

    def complete_project(self, project_id, result, artifacts):
        project_dir = self.project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        self._write_json(project_dir / "reader.json", artifacts["reader"])
        self._write_json(project_dir / "planner.json", artifacts["planner"])
        self._write_json(project_dir / "script.json", result["script"])
        self._write_text(project_dir / "script.yaml", result["yaml"])
        self._write_json(project_dir / "validation.json", result["validation"])
        self._write_json(project_dir / "agent_trace.json", result["agent_trace"])
        self._write_json(project_dir / "quality_metrics.json", result["quality_metrics"])
        self._write_json(project_dir / "initial_script.json", artifacts["initial_script"])
        self._write_json(
            project_dir / "initial_validation.json",
            artifacts["initial_validation"],
        )
        self._write_json(project_dir / "repair_history.json", artifacts["repair_history"])

        script = result["script"]
        status = result.get("final_status") or "completed"
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE projects SET
                    status = ?,
                    chapter_count = ?,
                    character_count = ?,
                    location_count = ?,
                    event_count = ?,
                    scene_count = ?,
                    parse_mode = ?,
                    parse_warning = ?,
                    error_message = ?,
                    current_step = ?,
                    progress = 100,
                    repair_count = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    len(script.get("chapters", [])),
                    len(script.get("characters", [])),
                    len(script.get("locations", [])),
                    len(script.get("events", [])),
                    len(script.get("scenes", [])),
                    result.get("parse_mode"),
                    result.get("parse_warning"),
                    None if status != "failed" else "自动修复两轮后仍未通过结构校验",
                    status,
                    result.get("repair_count", 0),
                    utc_now(),
                    utc_now(),
                    project_id,
                ),
            )

    def update_progress(self, project_id, current_step, progress, agent_trace=None):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET current_step = ?, progress = ?, updated_at = ?
                WHERE id = ?
                """,
                (current_step, progress, utc_now(), project_id),
            )
        if agent_trace is not None:
            self._write_json(
                self.project_dir(project_id) / "agent_trace.json",
                agent_trace,
            )

    def fail_project(self, project_id, error_message):
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET status = 'failed', current_step = 'failed', progress = 100,
                    error_message = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(error_message), now, now, project_id),
            )

    def interrupt_processing_projects(self):
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE projects
                SET status = 'interrupted', current_step = 'interrupted',
                    error_message = '服务重启导致任务中断',
                    finished_at = ?, updated_at = ?
                WHERE status = 'processing'
                """,
                (now, now),
            )
        return cursor.rowcount

    def list_projects(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            return None

        project = dict(row)
        project_dir = self.project_dir(project_id)
        project["source_text"] = self._read_text(project_dir / "source.txt")
        project["script"] = self._read_json(project_dir / "script.json")
        project["yaml"] = self._read_text(project_dir / "script.yaml")
        project["validation"] = self._read_json(project_dir / "validation.json")
        project["agent_trace"] = self._read_json(project_dir / "agent_trace.json")
        project["reader"] = self._read_json(project_dir / "reader.json")
        project["planner"] = self._read_json(project_dir / "planner.json")
        project["quality_metrics"] = self._read_json(
            project_dir / "quality_metrics.json"
        )
        project["initial_script"] = self._read_json(
            project_dir / "initial_script.json"
        )
        project["initial_validation"] = self._read_json(
            project_dir / "initial_validation.json"
        )
        project["repair_history"] = self._read_json(
            project_dir / "repair_history.json"
        )
        return project

    def delete_project(self, project_id):
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        if cursor.rowcount == 0:
            return False

        project_dir = self.project_dir(project_id).resolve()
        projects_root = self.projects_dir.resolve()
        if project_dir.parent == projects_root and project_dir.exists():
            shutil.rmtree(project_dir)
        return True

    def count_projects(self):
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM projects").fetchone()
        return row["count"]

    def project_dir(self, project_id):
        if not project_id or any(character not in "0123456789abcdef-" for character in project_id.lower()):
            raise ValueError("无效的项目 ID")
        return self.projects_dir / project_id

    @staticmethod
    def _write_json(path, value):
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_text(path, value):
        path.write_text(value or "", encoding="utf-8")

    @staticmethod
    def _read_json(path):
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_text(path):
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
