from __future__ import annotations

import subprocess
import sys

if __package__ in {None, ""}:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from telegram_jellyfin_bot.config import load_config
else:
    from .config import load_config


def main() -> int:
    try:
        config = load_config()
        executable = config.telegram_bot_api_exe_path
        if not executable.is_file():
            raise FileNotFoundError(
                f"telegram-bot-api.exe پیدا نشد:\n{executable}\n"
                "مسیر را در config.json اصلاح کنید."
            )
        command = [
            str(executable),
            f"--api-id={config.telegram_api_id}",
            f"--api-hash={config.telegram_api_hash}",
            "--local",
            f"--http-ip-address={config.local_bot_api_host}",
            f"--http-port={config.local_bot_api_port}",
        ]
        print(
            f"Local Bot API روی http://{config.local_bot_api_host}:"
            f"{config.local_bot_api_port} اجرا می‌شود."
        )
        # The data directory is the working directory, keeping server state portable.
        return subprocess.run(command, cwd=config.data_path, check=False).returncode
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
