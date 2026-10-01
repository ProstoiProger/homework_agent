from config import load_settings
from db import init_database


if __name__ == "__main__":
    settings = load_settings(require_runtime=False)
    init_database(settings.database_url)
    print("Database is ready")
