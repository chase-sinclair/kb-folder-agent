import asyncio
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


async def main() -> None:
    from agent.digest import start_digest_scheduler
    from ingestion.watcher import start_watcher
    from slack.bot import get_app, start_bot
    from storage.db import init_db

    await init_db()
    log.info("Database initialised")

    try:
        app = await get_app()
        await asyncio.gather(start_watcher(), start_bot(), start_digest_scheduler(app))
    except KeyboardInterrupt:
        log.info("Shutting down — keyboard interrupt")
    except Exception as exc:
        log.error("Fatal error: %s", exc)
        raise


def handle_connect() -> None:
    if len(sys.argv) < 3:
        print("Usage: python main.py connect <folder_path>")
        sys.exit(1)

    folder = Path(sys.argv[2]).resolve()

    if not folder.exists():
        print(f"Error: path does not exist: {folder}")
        sys.exit(1)

    if not folder.is_dir():
        print(f"Error: path is not a directory: {folder}")
        sys.exit(1)

    env_path = Path(__file__).parent / ".env"

    if env_path.exists():
        existing = env_path.read_text(encoding="utf-8")
        if re.search(r"^WATCHED_FOLDER=", existing, re.MULTILINE):
            replacement = f"WATCHED_FOLDER={folder}"
            updated = re.sub(
                r"^WATCHED_FOLDER=.*$",
                lambda _: replacement,
                existing,
                flags=re.MULTILINE,
            )
        else:
            updated = existing.rstrip("\n") + f"\nWATCHED_FOLDER={folder}\n"
    else:
        updated = f"WATCHED_FOLDER={folder}\n"

    env_path.write_text(updated, encoding="utf-8")

    print(f"Connected: {folder}")
    print("Run python main.py to start watching")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "connect":
        handle_connect()
    else:
        asyncio.run(main())
