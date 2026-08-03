from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """환경변수와 파일 경로를 한곳에서 관리합니다."""

    # 외부 API 키
    vworld_api_key: str = ""
    vworld_domain: str = ""
    building_api_key: str = ""

    # 입력 방식
    # True면 INPUT_DATA_PATH 파일을 사용하고, False면 POST JSON 주소를 사용합니다.
    input_file: bool = False
    input_data_path: Path = PROJECT_DIR / "test_data" / "candidate_addresses.csv"

    # 프로젝트 경로
    base_dir: Path = PROJECT_DIR
    base_merged_csv_path: Path = PROJECT_DIR / "data" / "input" / "Merged_Test_Data.csv"
    result_csv_path: Path = PROJECT_DIR / "data" / "result" / "Preprocessed_Candidates.csv"
    parcel_geojson_path: Path = PROJECT_DIR / "data" / "result" / "Preprocessed_Parcels.geojson"
    parcel_gpkg_path: Path = PROJECT_DIR / "data" / "result" / "Preprocessed_Parcels.gpkg"
    parcel_gpkg_layer: str = "parcels"

    # 사용자가 파일을 넣어 두는 폴더
    input_dir: Path = PROJECT_DIR / "data" / "input"
    solar_source_dir: Path = PROJECT_DIR / "data" / "source_data" / "solar"
    wind_source_dir: Path = PROJECT_DIR / "data" / "source_data" / "wind"
    dem_source_dir: Path = PROJECT_DIR / "data" / "source_data" / "dem"
    osm_source_dir: Path = PROJECT_DIR / "data" / "source_data" / "osm"
    gis_dir: Path = PROJECT_DIR / "data" / "gis"

    # 실행 중 생성 가능한 폴더
    work_dir: Path = PROJECT_DIR / "data" / "work"
    result_dir: Path = PROJECT_DIR / "data" / "result"

    # OSM 전력망
    osm_source_url: str = "https://download.geofabrik.de/asia/south-korea-latest.osm.pbf"
    osm_pbf_path: Path = PROJECT_DIR / "data" / "source_data" / "osm" / "south-korea-latest.osm.pbf"
    power_grid_gpkg_path: Path = PROJECT_DIR / "data" / "gis" / "korea_power_grid.gpkg"
    auto_download_osm: bool = True
    auto_build_power_grid: bool = True
    force_rebuild_power_grid: bool = False

    # API 처리 설정
    api_timeout_seconds: float = Field(default=40.0, gt=0)
    geocode_max_attempts: int = Field(default=5, ge=1, le=10)
    geocode_retry_interval_seconds: float = Field(default=2.0, ge=0)
    api_request_interval_seconds: float = Field(default=0.08, ge=0)
    max_batch_size: int = Field(default=500, ge=1, le=5000)

    # 공간 분석 설정
    solar_annual_to_daily_divisor: float = Field(default=365.25, gt=0)
    power_grid_projected_crs: str = "EPSG:5179"
    power_grid_search_radius_m: float = Field(default=5000.0, gt=0)
    high_voltage_threshold_kv: float = Field(default=154.0, gt=0)

    # 서버 설정
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8001, ge=1, le=65535)
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def resolved(self, path: Path) -> Path:
        """상대경로를 프로젝트 기준 절대경로로 변환합니다."""
        path = Path(path)
        return path if path.is_absolute() else (self.base_dir / path).resolve()


settings = Settings()
