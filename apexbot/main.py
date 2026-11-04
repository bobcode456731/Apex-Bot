"""Run Apex MiddleMan Services from the installed package or repository root."""

from __future__ import annotations

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

from .config import Settings


def main() -> None:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise SystemExit(f"Invalid bot configuration: {exc}") from exc

    if not settings.token:
        raise SystemExit(
            "DISCORD_TOKEN is missing. Set it in HOKAS environment variables, "
            "or copy .env.example to .env for local use."
        )

    # Import Discord only after configuration is validated so a missing token
    # gets an actionable startup message even before dependencies are installed.
    from .bot import run

    run(settings)


if __name__ == "__main__":
    main()
