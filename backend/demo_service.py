import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DEMO_PACKAGE_PATH = BASE_DIR / "demo" / "original_project.json"


def import_demo_project(project_store):
    package = json.loads(DEMO_PACKAGE_PATH.read_text(encoding="utf-8"))
    project_id = package["project_id"]
    existing = project_store.get_project(project_id)
    if existing:
        project_store.delete_project(project_id)

    project_store.create_project(
        project_id,
        package["title"],
        package["source_text"],
        package["source_filename"],
    )
    project_store.complete_project(
        project_id,
        package["result"],
        package["artifacts"],
    )
    return project_store.get_project(project_id)
