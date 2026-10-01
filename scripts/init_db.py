import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homework_agent.config import load_settings
from homework_agent.db import init_database


if __name__ == "__main__":
    settings = load_settings(require_runtime=False)
    init_database(settings.database_url)
    print("Database is ready")
