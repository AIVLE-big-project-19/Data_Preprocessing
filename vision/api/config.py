from dataclasses import dataclass
from pathlib import Path
import os

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    model_path: Path
    model_version: str
    candidate_gpkg_path: Path
    min_confidence: float = 0.50
    min_pixel_area: float = 700.0


def get_settings() -> Settings:
    """Read settings once while the application is starting."""
    _load_dotenv(PROJECT_DIR / ".env")
    model_path_value = os.getenv("MODEL_PATH")
    if not model_path_value:
        raise RuntimeError("MODEL_PATH environment variable must point to a YOLO .pt file.")

    model_path = Path(model_path_value)
    # A relative MODEL_PATH in .env is relative to the project directory,
    # independent of the directory from which Uvicorn was started.
    if not model_path.is_absolute():
        model_path = PROJECT_DIR / model_path

    return Settings(
        model_path=model_path.resolve(),
        model_version=os.getenv("MODEL_VERSION", model_path.stem),
        candidate_gpkg_path=Path(os.getenv(
            "CANDIDATE_GPKG_PATH",
            str(PROJECT_DIR / "data" / "candidate_parcels.gpkg"),
        )).expanduser().resolve(),
        min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.50")),
        min_pixel_area=float(os.getenv("MIN_PIXEL_AREA", "700")),
    )


def _load_dotenv(dotenv_path: Path) -> None:
    """Load simple KEY=VALUE pairs without requiring an extra dependency.

    Existing process environment variables win, so deployment configuration can
    safely override local development values in `.env`.
    """
    if not dotenv_path.is_file():
        return

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
