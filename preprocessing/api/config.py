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
    # VWorld 건물 WFS 레이어. 서비스 정책 변경 시 .env에서 덮어쓸 수 있습니다.
    vworld_building_typename: str = "lt_c_bldginfo"
    # 연속지적도 부번 레이어를 우선 사용하고, 코드에서 본번 레이어로 재시도합니다.
    vworld_parcel_typename: str = "lp_pa_cbnd_bubun"
    # 이전 공공데이터포털 키(호환성 보존, 현재 건물 판정에는 사용하지 않음)
    building_api_key: str = ""

    # 입력 방식
    # True면 INPUT_DATA_PATH 파일을 사용하고, False면 POST JSON 주소를 사용합니다.
    input_file: bool = False
    input_data_path: Path = PROJECT_DIR / "test_data" / "candidate_addresses.csv"

    # 프로젝트 경로
    base_dir: Path = PROJECT_DIR
    base_merged_csv_path: Path = PROJECT_DIR / "data" / "input" / "Merged_Test_Data.csv"
    result_csv_path: Path = PROJECT_DIR / "data" / "result" / "Merged_Test_Data.csv"
    latest_rows_csv_path: Path = PROJECT_DIR / "data" / "result" / "Latest_Merged_Rows.csv"
    audit_csv_path: Path = PROJECT_DIR / "data" / "result" / "Preprocessing_Audit.csv"
    parcel_geojson_path: Path = PROJECT_DIR / "data" / "result" / "Preprocessed_Parcels.geojson"
    parcel_gpkg_path: Path = PROJECT_DIR / "data" / "result" / "Preprocessed_Parcels.gpkg"
    parcel_gpkg_layer: str = "parcels"
    vision_features_csv_path: Path = PROJECT_DIR / "data" / "result" / "Vision_Features.csv"
    vision_combined_csv_path: Path = PROJECT_DIR / "data" / "result" / "Latest_Merged_Rows_With_Vision.csv"

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
    max_batch_size: int = Field(default=1000, ge=1, le=5000)
    # False이면 일부 조회가 실패해도 생성된 34컬럼 CSV와 감사 파일을 저장합니다.
    # 완전성 여부와 누락 컬럼은 Preprocessing_Audit.csv에서 확인할 수 있습니다.
    require_complete_merged_rows: bool = False

    # Vision AI 연동
    vision_enabled: bool = True
    vision_api_url: str = "http://127.0.0.1:8000/predict"
    vision_timeout_seconds: float = Field(default=180.0, gt=0)
    vision_image_zoom: int = Field(default=18, ge=7, le=18)
    vision_image_size: int = Field(default=1024, ge=256, le=2048)

    # 공간 분석 설정
    solar_annual_to_daily_divisor: float = Field(default=365.25, gt=0)

    # 지형 피처 설정
    # 업로드한 노트북과 동일하게 기본 3x3 윈도우를 사용합니다.
    terrain_window_size: int = Field(default=3, ge=1, le=15)
    # False이면 별도 slope/aspect/hillshade TIFF 없이 DEM 주변 픽셀에서 즉석 계산합니다.
    terrain_auto_build_derivatives: bool = False
    terrain_scale_factor: float = Field(default=111120.0, gt=0)

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
