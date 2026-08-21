from __future__ import annotations

import gc
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import fiona
import geopandas as gpd
import httpx
import numpy as np
import pandas as pd
import rasterio
from filelock import FileLock
from pyproj import CRS, Transformer
from rasterio.errors import RasterioIOError
from shapely import wkt
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from .config import settings
from .terrain import TERRAIN_FEATURE_NAMES, extract_terrain_features


KST = timezone(timedelta(hours=9))
BUILDING_API_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
VWORLD_SEARCH_URL = "https://api.vworld.kr/req/search"
VWORLD_PARCEL_URL = "https://api.vworld.kr/ned/wfs/getCtnlgsSpceWFS"
VWORLD_BUILDING_URL = "https://api.vworld.kr/req/wfs"
VWORLD_IMAGE_URL = "https://api.vworld.kr/req/image"

ADDRESS_ALIASES = ["address", "address_ml", "주소", "소재지", "road_address", "lot_address", "도로명주소", "지번주소"]

CANONICAL_SIDO_ALIASES = {
    "서울": "서울특별시", "서울특별시": "서울특별시",
    "부산": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구광역시": "대구광역시",
    "인천": "인천광역시", "인천광역시": "인천광역시",
    "광주": "광주광역시", "광주광역시": "광주광역시",
    "대전": "대전광역시", "대전광역시": "대전광역시",
    "울산": "울산광역시", "울산광역시": "울산광역시",
    "세종": "세종특별자치시", "세종특별자치시": "세종특별자치시",
    "경기": "경기도", "경기도": "경기도",
    "강원": "강원특별자치도", "강원도": "강원특별자치도", "강원특별자치도": "강원특별자치도",
    "충북": "충청북도", "충청북도": "충청북도",
    "충남": "충청남도", "충청남도": "충청남도",
    "전북": "전북특별자치도", "전라북도": "전북특별자치도", "전북특별자치도": "전북특별자치도",
    "전남": "전라남도", "전라남도": "전라남도",
    "경북": "경상북도", "경상북도": "경상북도",
    "경남": "경상남도", "경상남도": "경상남도",
    "제주": "제주특별자치도", "제주특별자치도": "제주특별자치도",
}

METROPOLITAN_SIDO = {
    "서울특별시", "부산광역시", "대구광역시", "인천광역시",
    "광주광역시", "대전광역시", "울산광역시",
}

RASTER_FEATURES = [
    "ghi_avg_daily",
    "pvout_avg_daily",
    "dni_avg_daily",
    "dif_avg_daily",
    "gti_avg_daily",
    "temp_avg",
    "wind_speed_10m",
    "wind_speed_50m",
    "wind_speed_100m",
    "elevation_avg",
    "slope_avg",
    "slope_dir",
    "Hillshade",
    "Southness",
]


MERGED_OUTPUT_COLUMNS = [
    "source_id_ml", "address_ml", "longitude", "latitude", "시도", "시군구",
    "자산구분_ML", "설치구분", "label",
    "ghi_avg_daily", "pvout_avg_daily", "dni_avg_daily", "dif_avg_daily", "gti_avg_daily", "temp_avg",
    "wind_speed_10m", "wind_speed_50m", "wind_speed_100m",
    "slope_avg", "slope_dir", "elevation_avg", "Hillshade", "Southness",
    "distance_to_substation_km", "distance_to_powerline_km",
    "substation_count_5km", "powerline_length_5km_km", "high_voltage_line_nearby_5km",
    "substation_max_voltage_kv", "powerline_max_voltage_kv",
    "substation_max_voltage_kv_missing", "powerline_max_voltage_kv_missing",
    "asset_type_code", "region_group",
]

CONDITIONAL_MISSING_COLUMNS = {
    "substation_max_voltage_kv": "substation_max_voltage_kv_missing",
    "powerline_max_voltage_kv": "powerline_max_voltage_kv_missing",
}

SOLAR_ANNUAL_TOTAL_FEATURES = {
    "ghi_avg_daily",
    "pvout_avg_daily",
    "dni_avg_daily",
    "dif_avg_daily",
    "gti_avg_daily",
}

GRID_FEATURES = [
    "distance_to_substation_km",
    "distance_to_powerline_km",
    "substation_count_5km",
    "powerline_length_5km_km",
    "high_voltage_line_nearby_5km",
    "substation_max_voltage_kv",
    "powerline_max_voltage_kv",
    "substation_max_voltage_kv_missing",
    "powerline_max_voltage_kv_missing",
]

PARCEL_COLUMNS = ["pnu", "lot_number", "land_category", "parcel_area_m2"]
BUILDING_COLUMNS = [
    "candidate_type_api",
    "building_count",
    "main_purpose_names",
    "plat_area_max",
    "arch_area_sum",
    "total_area_sum",
    "building_checked_at",
]

VISION_COLUMNS = [
    "pixel_area", "real_area",
    "distance_to_road_px", "distance_to_building_px",
    "distance_to_road_m", "distance_to_building_m",
    "shape_score", "shape_grade", "shape_efficiency",
    "recommended_layout", "usable_area", "estimated_panel_count",
    "candidate_id", "pnu",
]
VISION_AUDIT_COLUMNS = ["vision_status", "vision_error", "vision_model_version"]

AUXILIARY_COLUMNS = [
    "address_original",
    "matched_address",
    "matched_category",
    "vworld_item_id",
    *PARCEL_COLUMNS,
    *BUILDING_COLUMNS,
    "updated_at",
]

STATUS_COLUMNS = [
    "processing_status",
    "change_status",
    "coordinate_status",
    "geocode_status",
    "geocode_attempt_count",
    "geocode_error",
    "raster_status",
    "terrain_status",
    "polygon_status",
    "polygon_error",
    "building_status",
    "building_error",
    "grid_status",
    "warning_message",
    "error_message",
]

# 새 조회가 정상적으로 끝났을 때만 기존 값을 교체하는 단계별 컬럼입니다.
GEOCODE_UPDATE_COLUMNS = [
    "address_ml",
    "longitude",
    "latitude",
    "시도",
    "시군구",
    "region_group",
    "matched_address",
    "matched_category",
    "vworld_item_id",
]


class PipelineError(ValueError):
    """입력값 또는 기준 데이터 오류입니다."""


class ResourceError(RuntimeError):
    """필수 리소스를 읽거나 준비하지 못한 경우입니다."""


class RuntimeResources:
    """서버에서 재사용할 무거운 리소스를 보관합니다."""

    def __init__(self) -> None:
        self.http_client: httpx.AsyncClient | None = None
        self.raster_paths: dict[str, Path | None] = {}
        self.raster_datasets: dict[str, rasterio.io.DatasetReader] = {}
        self.transformers: dict[str, Transformer] = {}
        self.substations: gpd.GeoDataFrame | None = None
        self.powerlines: gpd.GeoDataFrame | None = None
        self.substation_voltage_columns: list[str] = []
        self.powerline_voltage_columns: list[str] = []
        self.initialization_warnings: list[str] = []


resources = RuntimeResources()


def now_kst_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def normalize_address(value: Any) -> str | None:
    """입력/저장 주소를 정리하고 시도 약칭을 정식 명칭으로 통일합니다."""
    text = normalize_text(value)
    if text is None:
        return None

    text = re.sub(r"(?<=\d)\s*번지\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = text.split()
    if tokens and tokens[0] in CANONICAL_SIDO_ALIASES:
        tokens[0] = CANONICAL_SIDO_ALIASES[tokens[0]]
        text = " ".join(tokens)
    return text or None


def normalize_address_key(value: Any) -> str | None:
    """공백과 행정구역 표기 차이를 무시하는 주소 비교 키입니다."""
    text = normalize_address(value)
    if text is None:
        return None
    return re.sub(r"\s+", "", text)


def find_column(columns: Sequence[str], aliases: Sequence[str]) -> str | None:
    lookup = {str(column).strip().lower(): str(column) for column in columns}
    for alias in aliases:
        if alias.lower() in lookup:
            return lookup[alias.lower()]
    return None


def _read_csv_bytes(content: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return pd.read_csv(io.BytesIO(content), encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise PipelineError("CSV 인코딩을 인식할 수 없습니다. UTF-8-SIG 또는 CP949를 사용하세요.")


def read_address_file(filename: str, content: bytes) -> list[str]:
    """CSV/XLSX에서 주소 목록만 안전하게 읽습니다."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        raw = _read_csv_bytes(content)
    elif suffix in {".xlsx", ".xls"}:
        raw = pd.read_excel(io.BytesIO(content))
    else:
        raise PipelineError("입력 파일은 CSV, XLSX 또는 XLS 형식이어야 합니다.")

    address_column = find_column(raw.columns, ADDRESS_ALIASES)
    if address_column is None:
        raise PipelineError(f"주소 컬럼을 찾지 못했습니다. 현재 컬럼: {raw.columns.tolist()}")
    return prepare_addresses(raw[address_column].tolist())


def read_address_path(path: Path) -> list[str]:
    """설정된 CSV/XLSX/XLS 파일에서 주소 목록을 읽습니다."""
    resolved = settings.resolved(path)
    if not resolved.exists():
        raise PipelineError(f"입력 파일이 없습니다: {resolved}")
    if not resolved.is_file():
        raise PipelineError(f"입력 경로가 파일이 아닙니다: {resolved}")
    return read_address_file(resolved.name, resolved.read_bytes())


def resolve_input_addresses(request_addresses: Iterable[Any]) -> list[str]:
    """환경 설정에 따라 파일 또는 POST JSON 주소를 선택합니다."""
    if settings.input_file:
        return read_address_path(settings.input_data_path)
    return prepare_addresses(request_addresses)


def prepare_addresses(addresses: Iterable[Any]) -> list[str]:
    """빈 주소를 제거하고 입력 내부 중복은 마지막 값을 유지합니다."""
    prepared: list[tuple[str, str]] = []
    for value in addresses:
        normalized = normalize_address(value)
        key = normalize_address_key(normalized)
        if normalized and key:
            prepared.append((key, normalized))

    if not prepared:
        raise PipelineError("유효한 주소가 한 건도 없습니다.")

    last_by_key: dict[str, str] = {}
    order: list[str] = []
    for key, address in prepared:
        if key not in last_by_key:
            order.append(key)
        last_by_key[key] = address

    result = [last_by_key[key] for key in order]
    if len(result) > settings.max_batch_size:
        raise PipelineError(f"한 번에 처리할 수 있는 주소는 최대 {settings.max_batch_size}건입니다.")
    return result


def parse_region(address: Any) -> tuple[str | None, str | None]:
    """Merged_Test_Data의 시도/시군구 표기 규칙에 맞춰 행정구역을 만듭니다."""
    text = normalize_text(address)
    if not text:
        return None, None

    tokens = text.split()
    sido: str | None = None
    sido_index: int | None = None
    for index, token in enumerate(tokens):
        canonical = CANONICAL_SIDO_ALIASES.get(token)
        if canonical:
            sido = canonical
            sido_index = index
            break

    # VWorld matched_address에 시도가 포함되는 것이 정상 경로다.
    search_tokens = tokens[sido_index + 1 :] if sido_index is not None else tokens

    if sido == "세종특별자치시":
        return sido, "세종특별자치시"

    admin = [token for token in search_tokens if token.endswith(("시", "군", "구"))]
    if not admin:
        return sido, None

    if sido in METROPOLITAN_SIDO:
        # 광역시는 서구/중구/유성구처럼 하위 군·구를 사용한다.
        for token in admin:
            if token.endswith(("군", "구")):
                return sido, token
        return sido, admin[0]

    first = admin[0]
    if first.endswith("시") and len(admin) >= 2 and admin[1].endswith("구"):
        # 청주시 상당구, 천안시 동남구처럼 시+구를 함께 보존한다.
        return sido, f"{first} {admin[1]}"
    return sido, first


def _find_first_file(directory: Path, patterns: Sequence[str], exclude_tokens: Sequence[str] = ()) -> Path | None:
    if not directory.exists():
        return None
    found: list[Path] = []
    for pattern in patterns:
        found.extend(directory.glob(pattern))
        found.extend(directory.glob(f"**/{pattern}"))
    unique = {
        path.resolve()
        for path in found
        if path.is_file() and not any(token.lower() in path.name.lower() for token in exclude_tokens)
    }
    ordered = sorted(unique, key=lambda path: (len(str(path)), str(path).lower()))
    return ordered[0] if ordered else None


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_solar_extract_dir() -> Path:
    extract_dir = settings.resolved(settings.work_dir) / "rasters" / "global_solar_atlas"
    extract_dir.mkdir(parents=True, exist_ok=True)

    solar_dir = settings.resolved(settings.solar_source_dir)
    solar_source = _find_first_file(
        solar_dir,
        ["*YearlyMonthlyTotals*GlobalSolarAtlas*.zip", "*YearlyMonthlyTotals*.zip", "*.zip"],
    )
    if solar_source is None:
        return extract_dir

    if not zipfile.is_zipfile(solar_source):
        return extract_dir

    current_hash = _sha256_file(solar_source)
    marker = extract_dir / ".source_hash"
    previous_hash = marker.read_text(encoding="utf-8").strip() if marker.exists() else None
    if current_hash == previous_hash:
        return extract_dir

    # 원천 ZIP이 바뀐 경우에만 기존 압축 해제 결과를 교체합니다.
    for old_path in sorted(extract_dir.rglob("*"), reverse=True):
        if old_path == marker:
            continue
        if old_path.is_file():
            old_path.unlink()
        elif old_path.is_dir():
            with suppress(OSError):
                old_path.rmdir()

    with zipfile.ZipFile(solar_source, "r") as archive:
        archive.extractall(extract_dir)
    marker.write_text(current_hash, encoding="utf-8")
    return extract_dir


def _prepare_terrain_rasters(dem_dir: Path) -> dict[str, Path | None]:
    """
    DEM과 파생 래스터를 찾고, 필요한 경우 업로드 노트북과 같은
    gdaldem 명령으로 slope/aspect/hillshade를 생성합니다.

    gdaldem이 없는 환경에서는 기존에 준비된 파생 GeoTIFF만 사용합니다.
    """
    dem_path = _find_first_file(
        dem_dir,
        ["*output_hh*.tif*", "*elevation*.tif*", "*dem*.tif*"],
        ["slope", "aspect", "hillshade", "southness"],
    )
    slope_path = _find_first_file(
        dem_dir,
        ["*slope_avg*.tif*", "*slope*.tif*"],
        ["slope_dir", "aspect", "southness"],
    )
    aspect_path = _find_first_file(dem_dir, ["*slope_dir*.tif*", "*aspect*.tif*"])
    hillshade_path = _find_first_file(dem_dir, ["*hillshade*.tif*"])

    result = {
        "elevation_avg": dem_path,
        "slope_avg": slope_path,
        "slope_dir": aspect_path,
        "Hillshade": hillshade_path,
        # Southness는 3x3 aspect 원형평균에서 직접 계산하므로 별도 래스터가 필요 없습니다.
        "Southness": None,
    }

    if dem_path is None or not settings.terrain_auto_build_derivatives:
        return result

    missing = [
        name for name in ("slope_avg", "slope_dir", "Hillshade")
        if result[name] is None
    ]
    if not missing:
        return result

    gdaldem = shutil.which("gdaldem")
    if gdaldem is None:
        resources.initialization_warnings.append(
            "지형 파생 래스터가 일부 없고 gdaldem 실행 파일도 없습니다. "
            "GDAL CLI를 설치하거나 slope/aspect/hillshade GeoTIFF를 data/source_data/dem에 넣으세요."
        )
        return result

    output_dir = settings.resolved(settings.work_dir) / "rasters" / "terrain"
    output_dir.mkdir(parents=True, exist_ok=True)

    commands = {
        "slope_avg": [
            gdaldem, "slope", str(dem_path), str(output_dir / "slope.tif"),
            "-s", str(settings.terrain_scale_factor), "-compute_edges",
        ],
        "slope_dir": [
            gdaldem, "aspect", str(dem_path), str(output_dir / "aspect.tif"),
            "-compute_edges",
        ],
        "Hillshade": [
            gdaldem, "hillshade", str(dem_path), str(output_dir / "hillshade.tif"),
            "-s", str(settings.terrain_scale_factor), "-compute_edges",
        ],
    }

    for feature in missing:
        command = commands[feature]
        target = Path(command[3])
        try:
            if not target.exists():
                subprocess.run(command, check=True, capture_output=True, text=True)
            if target.exists():
                result[feature] = target
        except Exception as exc:
            resources.initialization_warnings.append(
                f"지형 파생 래스터 생성 실패({feature}): {type(exc).__name__}: {exc}"
            )

    return result


def discover_raster_paths() -> dict[str, Path | None]:
    """원천 폴더에서 Solar/Wind/DEM 래스터를 찾습니다."""
    solar_extract_dir = _prepare_solar_extract_dir()
    wind_dir = settings.resolved(settings.wind_source_dir)
    dem_dir = settings.resolved(settings.dem_source_dir)
    terrain_paths = _prepare_terrain_rasters(dem_dir)

    def solar(*keywords: str) -> Path | None:
        candidates = []
        if solar_extract_dir.exists():
            for path in solar_extract_dir.rglob("*"):
                if path.suffix.lower() not in {".tif", ".tiff"}:
                    continue
                lower = path.name.lower()
                if any(keyword.lower() in lower for keyword in keywords):
                    candidates.append(path)
        return sorted(candidates, key=lambda path: (len(str(path)), str(path).lower()))[0] if candidates else None

    return {
        "ghi_avg_daily": solar("ghi"),
        "pvout_avg_daily": solar("pvout"),
        "dni_avg_daily": solar("dni"),
        "dif_avg_daily": solar("dif"),
        "gti_avg_daily": solar("gti"),
        "temp_avg": solar("temp"),
        "wind_speed_10m": _find_first_file(wind_dir, ["*wind-speed_10m*.tif*", "*wind_speed_10m*.tif*", "*10m*.tif*"]),
        "wind_speed_50m": _find_first_file(wind_dir, ["*wind-speed_50m*.tif*", "*wind_speed_50m*.tif*", "*50m*.tif*"]),
        "wind_speed_100m": _find_first_file(wind_dir, ["*wind-speed_100m*.tif*", "*wind_speed_100m*.tif*", "*100m*.tif*"]),
        **terrain_paths,
    }


async def _download_osm_if_needed() -> Path:
    destination = settings.resolved(settings.osm_pbf_path)
    if destination.exists() and destination.stat().st_size >= 10_000_000:
        return destination
    if not settings.auto_download_osm:
        raise ResourceError(f"OSM PBF가 없습니다: {destination}")
    if not destination.parent.exists():
        raise ResourceError(f"OSM 원천 폴더가 없습니다: {destination.parent}")
    if resources.http_client is None:
        raise ResourceError("HTTP 클라이언트가 초기화되지 않았습니다.")

    temp_path = destination.with_suffix(destination.suffix + ".part")
    temp_path.unlink(missing_ok=True)
    async with resources.http_client.stream("GET", settings.osm_source_url, timeout=None) as response:
        response.raise_for_status()
        with temp_path.open("wb") as output:
            async for chunk in response.aiter_bytes(1024 * 1024):
                output.write(chunk)
    if not temp_path.exists() or temp_path.stat().st_size < 10_000_000:
        temp_path.unlink(missing_ok=True)
        raise ResourceError("다운로드한 OSM PBF가 비정상적으로 작습니다.")
    os.replace(temp_path, destination)
    return destination


def _clean_osm_gdf(gdf: gpd.GeoDataFrame | None, wanted_columns: list[str]) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    result = gdf.loc[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    existing = [column for column in wanted_columns if column in result.columns]
    if "geometry" not in existing:
        existing.append("geometry")
    result = result[existing].copy()
    for column in result.columns:
        if column != "geometry":
            result[column] = result[column].astype("string")
    if result.crs is None:
        result = result.set_crs("EPSG:4326")
    return result


def _require_pyrosm():
    try:
        from pyrosm import OSM
    except ImportError as exc:
        raise ResourceError("pyrosm이 설치되어 있지 않습니다.") from exc
    return OSM


def _run_checked(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


def _build_power_grid_gpkg(source_pbf: Path, cache_path: Path) -> None:
    """OSM PBF에서 변전소와 전력선 레이어를 생성합니다."""
    if not cache_path.parent.exists():
        raise ResourceError(f"GIS 폴더가 없습니다: {cache_path.parent}")

    OSM = _require_pyrosm()
    osmium_path = shutil.which("osmium")
    work_osm_dir = settings.resolved(settings.work_dir) / "osm"
    work_osm_dir.mkdir(parents=True, exist_ok=True)
    substation_pbf = work_osm_dir / "korea_substations.osm.pbf"
    powerline_pbf = work_osm_dir / "korea_powerlines.osm.pbf"

    if osmium_path:
        if not substation_pbf.exists() or substation_pbf.stat().st_size == 0:
            _run_checked([osmium_path, "tags-filter", str(source_pbf), "nwr/power=substation", "-o", str(substation_pbf), "--overwrite"])
        if not powerline_pbf.exists() or powerline_pbf.stat().st_size == 0:
            _run_checked([
                osmium_path,
                "tags-filter",
                str(source_pbf),
                "nwr/power=line",
                "nwr/power=minor_line",
                "-o",
                str(powerline_pbf),
                "--overwrite",
            ])
        substation_source = substation_pbf
        powerline_source = powerline_pbf
    else:
        substation_source = source_pbf
        powerline_source = source_pbf

    def extract_substations(path: Path) -> gpd.GeoDataFrame:
        osm = OSM(str(path), engine="out_of_core", workers=1)
        result = osm.get_data_by_custom_criteria(
            custom_filter={"power": ["substation"]},
            filter_type="keep",
            keep_nodes=True,
            keep_ways=True,
            keep_relations=True,
            tags_as_columns=["name", "power", "substation", "voltage", "operator", "ref"],
            keep_other_tags=False,
        )
        del osm
        gc.collect()
        return _clean_osm_gdf(result, ["id", "name", "power", "substation", "voltage", "operator", "ref", "geometry"])

    def extract_powerlines(path: Path) -> gpd.GeoDataFrame:
        osm = OSM(str(path), engine="out_of_core", workers=1)
        result = osm.get_data_by_custom_criteria(
            custom_filter={"power": ["line", "minor_line"]},
            filter_type="keep",
            keep_nodes=False,
            keep_ways=True,
            keep_relations=False,
            tags_as_columns=["name", "power", "voltage", "operator", "ref", "circuits", "cables", "frequency"],
            keep_other_tags=False,
        )
        del osm
        gc.collect()
        return _clean_osm_gdf(result, ["id", "name", "power", "voltage", "operator", "ref", "circuits", "cables", "frequency", "geometry"])

    substations = extract_substations(substation_source)
    if substations.empty:
        raise ResourceError("OSM 변전소 추출 결과가 없습니다.")
    powerlines = extract_powerlines(powerline_source)
    if powerlines.empty:
        raise ResourceError("OSM 전력선 추출 결과가 없습니다.")

    temp_gpkg = cache_path.with_name(cache_path.stem + ".tmp.gpkg")
    temp_gpkg.unlink(missing_ok=True)
    substations.to_file(temp_gpkg, layer="substations", driver="GPKG")
    powerlines.to_file(temp_gpkg, layer="powerlines", driver="GPKG")
    os.replace(temp_gpkg, cache_path)


async def _prepare_power_grid_file() -> Path | None:
    gpkg_path = settings.resolved(settings.power_grid_gpkg_path)
    if gpkg_path.exists() and gpkg_path.stat().st_size > 0 and not settings.force_rebuild_power_grid:
        return gpkg_path
    if not settings.auto_build_power_grid:
        return None

    pbf_path = await _download_osm_if_needed()
    if gpkg_path.exists():
        gpkg_path.unlink()
    _build_power_grid_gpkg(pbf_path, gpkg_path)
    return gpkg_path


def parse_voltage_kv(value: Any) -> float:
    if value is None:
        return np.nan
    try:
        if pd.isna(value):
            return np.nan
    except (TypeError, ValueError):
        pass
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value).strip())
    if not numbers:
        return np.nan
    maximum = max(float(number) for number in numbers)
    return maximum / 1000.0 if maximum > 1000 else maximum


def find_voltage_columns(gdf: gpd.GeoDataFrame | None) -> list[str]:
    if gdf is None:
        return []
    return [column for column in gdf.columns if "voltage" in str(column).lower() or "전압" in str(column)]


def _row_max_voltage_kv(row: pd.Series, columns: list[str]) -> float:
    values = [parse_voltage_kv(row[column]) for column in columns if column in row.index]
    values = [float(value) for value in values if pd.notna(value)]
    return max(values) if values else np.nan


def _gdf_max_voltage_kv(gdf: gpd.GeoDataFrame | None, columns: list[str]) -> float:
    if gdf is None or gdf.empty or not columns:
        return np.nan
    values = [_row_max_voltage_kv(row, columns) for _, row in gdf.iterrows()]
    values = [float(value) for value in values if pd.notna(value)]
    return max(values) if values else np.nan


def _load_power_layers(gpkg_path: Path) -> tuple[gpd.GeoDataFrame | None, gpd.GeoDataFrame | None]:
    layers = list(fiona.listlayers(gpkg_path))
    substation_layer = next((layer for layer in layers if "substation" in layer.lower() or "변전소" in layer.lower()), None)
    powerline_layer = next(
        (
            layer
            for layer in layers
            if "powerline" in layer.lower()
            or "power_line" in layer.lower()
            or "전력선" in layer.lower()
            or "송전선" in layer.lower()
            or layer.lower() in {"line", "lines"}
        ),
        None,
    )
    substations = gpd.read_file(gpkg_path, layer=substation_layer) if substation_layer else None
    powerlines = gpd.read_file(gpkg_path, layer=powerline_layer) if powerline_layer else None
    return substations, powerlines


def _prepare_power_gdf(gdf: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame | None:
    if gdf is None or gdf.empty:
        return None
    if gdf.crs is None:
        raise ResourceError("전력망 레이어에 CRS가 없습니다.")
    result = gdf.loc[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if result.empty:
        return None
    return result.to_crs(settings.power_grid_projected_crs).reset_index(drop=True)


async def initialize_resources() -> None:
    """서버 시작 시 재사용할 HTTP, 래스터, 전력망 리소스를 준비합니다."""
    resources.initialization_warnings.clear()

    # 사용자가 채워야 하는 폴더는 코드에서 만들지 않고 존재 여부만 확인합니다.
    required_dirs = [
        settings.resolved(settings.input_dir),
        settings.resolved(settings.solar_source_dir),
        settings.resolved(settings.wind_source_dir),
        settings.resolved(settings.dem_source_dir),
        settings.resolved(settings.osm_source_dir),
        settings.resolved(settings.gis_dir),
    ]
    missing_dirs = [str(path) for path in required_dirs if not path.exists()]
    if missing_dirs:
        raise ResourceError(f"필수 입력 폴더가 없습니다: {missing_dirs}")

    # 작업·결과 폴더만 자동 생성합니다.
    settings.resolved(settings.work_dir).mkdir(parents=True, exist_ok=True)
    settings.resolved(settings.result_dir).mkdir(parents=True, exist_ok=True)

    resources.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.api_timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": "solar-preprocessing-api/1.0"},
    )

    resources.raster_paths = discover_raster_paths()
    for name, path in resources.raster_paths.items():
        if path is None or not path.exists():
            # 지형 파생값은 DEM 주변 픽셀에서 즉석 계산할 수 있으므로 누락 경고를 내지 않습니다.
            terrain_derivatives = {"slope_avg", "slope_dir", "Hillshade", "Southness"}
            if name not in terrain_derivatives:
                resources.initialization_warnings.append(f"래스터 누락: {name}")
            continue
        try:
            dataset = rasterio.open(path)
            resources.raster_datasets[name] = dataset
            crs_key = str(dataset.crs)
            if crs_key not in resources.transformers and CRS.from_user_input(dataset.crs) != CRS.from_epsg(4326):
                resources.transformers[crs_key] = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        except RasterioIOError as exc:
            resources.initialization_warnings.append(f"래스터 열기 실패: {name}: {exc}")

    try:
        gpkg_path = await _prepare_power_grid_file()
        if gpkg_path and gpkg_path.exists():
            substations, powerlines = _load_power_layers(gpkg_path)
            resources.substations = _prepare_power_gdf(substations)
            resources.powerlines = _prepare_power_gdf(powerlines)
            resources.substation_voltage_columns = find_voltage_columns(resources.substations)
            resources.powerline_voltage_columns = find_voltage_columns(resources.powerlines)
        else:
            resources.initialization_warnings.append("전력망 GPKG가 없습니다.")
    except Exception as exc:
        resources.initialization_warnings.append(f"전력망 준비 실패: {type(exc).__name__}: {exc}")


async def close_resources() -> None:
    for dataset in resources.raster_datasets.values():
        with suppress(Exception):
            dataset.close()
    resources.raster_datasets.clear()
    resources.transformers.clear()
    if resources.http_client is not None:
        await resources.http_client.aclose()
        resources.http_client = None


def get_source_status() -> dict[str, Any]:
    base_csv = settings.resolved(settings.base_merged_csv_path)
    result_csv = settings.resolved(settings.result_csv_path)
    gpkg = settings.resolved(settings.power_grid_gpkg_path)
    input_path = settings.resolved(settings.input_data_path)
    return {
        "input": {
            "mode": "file" if settings.input_file else "json",
            "path": str(input_path) if settings.input_file else None,
            "exists": input_path.exists() if settings.input_file else None,
        },
        "api_keys": {
            "vworld": bool(settings.vworld_api_key.strip()),
            "building": bool(settings.vworld_api_key.strip()),
        },
        "candidate_type": {
            "source": "vworld_building_wfs",
            "typename": settings.vworld_building_typename,
        },
        "files": {
            "base_merged_csv": {"path": str(base_csv), "exists": base_csv.exists()},
            "result_csv": {"path": str(result_csv), "exists": result_csv.exists()},
            "power_grid_gpkg": {"path": str(gpkg), "exists": gpkg.exists()},
        },
        "rasters": {
            name: {"path": str(path) if path else None, "loaded": name in resources.raster_datasets}
            for name, path in resources.raster_paths.items()
        },
        "terrain": {
            "window_size": settings.terrain_window_size,
            "auto_build_derivatives": settings.terrain_auto_build_derivatives,
            "scale_factor": settings.terrain_scale_factor,
            "southness_source": "circular_mean_aspect",
        },
        "power_grid": {
            "substations": 0 if resources.substations is None else len(resources.substations),
            "powerlines": 0 if resources.powerlines is None else len(resources.powerlines),
        },
        "warnings": list(resources.initialization_warnings),
    }


async def _vworld_geocode_once(address: str, category: str) -> dict[str, Any]:
    if not settings.vworld_api_key.strip():
        return {"success": False, "status": "MISSING_API_KEY", "geocode_error": "VWorld API 키가 없습니다."}
    if resources.http_client is None:
        raise ResourceError("HTTP 클라이언트가 초기화되지 않았습니다.")

    response = await resources.http_client.get(
        VWORLD_SEARCH_URL,
        params={
            "service": "search",
            "request": "search",
            "version": "2.0",
            "crs": "EPSG:4326",
            "size": 10,
            "page": 1,
            "query": address,
            "type": "address",
            "category": category,
            "format": "json",
            "errorformat": "json",
            "key": settings.vworld_api_key,
            "domain": settings.vworld_domain,
        },
    )
    response.raise_for_status()
    payload = response.json()
    body = payload.get("response", {})
    status = body.get("status")
    if status != "OK":
        error = body.get("error", {}) or {}
        return {
            "success": False,
            "status": status or "NO_RESULT",
            "geocode_error": error.get("text") or error.get("level") or status or "NO_RESULT",
        }

    items = body.get("result", {}).get("items", [])
    if not items:
        return {"success": False, "status": "NO_RESULT", "geocode_error": f"{category}:NO_RESULT"}

    item = items[0]
    point = item.get("point", {}) or {}
    longitude = pd.to_numeric(point.get("x"), errors="coerce")
    latitude = pd.to_numeric(point.get("y"), errors="coerce")
    if pd.isna(longitude) or pd.isna(latitude):
        return {"success": False, "status": "INVALID_COORDINATE", "geocode_error": f"{category}:INVALID_COORDINATE"}

    address_info = item.get("address", {}) or {}
    return {
        "success": True,
        "status": "SUCCESS",
        "longitude": float(longitude),
        "latitude": float(latitude),
        "matched_address": address_info.get("road") or address_info.get("parcel") or item.get("title"),
        "matched_category": category,
        "vworld_item_id": item.get("id"),
        "geocode_error": None,
    }


async def geocode_with_retry(address: str) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, settings.geocode_max_attempts + 1):
        for category in ("road", "parcel"):
            try:
                result = await _vworld_geocode_once(address, category)
                longitude = pd.to_numeric(result.get("longitude"), errors="coerce")
                latitude = pd.to_numeric(result.get("latitude"), errors="coerce")
                valid = result.get("success") is True and pd.notna(longitude) and pd.notna(latitude)
                if valid and -180 <= float(longitude) <= 180 and -90 <= float(latitude) <= 90:
                    result["geocode_attempt_count"] = attempt
                    result["geocode_status"] = "SUCCESS"
                    return result
                errors.append(f"{attempt}회차/{category}:{result.get('status')}:{result.get('geocode_error')}")
            except Exception as exc:
                errors.append(f"{attempt}회차/{category}:{type(exc).__name__}:{exc}")
            if settings.api_request_interval_seconds:
                await _async_sleep(settings.api_request_interval_seconds)

        if attempt < settings.geocode_max_attempts and settings.geocode_retry_interval_seconds:
            await _async_sleep(settings.geocode_retry_interval_seconds)

    return {
        "success": False,
        "status": "FAILED_AFTER_RETRY",
        "longitude": np.nan,
        "latitude": np.nan,
        "matched_address": None,
        "matched_category": None,
        "vworld_item_id": None,
        "geocode_attempt_count": settings.geocode_max_attempts,
        "geocode_status": "FAILED_AFTER_RETRY",
        "geocode_error": " | ".join(errors),
    }


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def extract_raster_features(longitude: Any, latitude: Any) -> dict[str, Any]:
    longitude = pd.to_numeric(longitude, errors="coerce")
    latitude = pd.to_numeric(latitude, errors="coerce")
    if pd.isna(longitude) or pd.isna(latitude):
        return {
            **{name: np.nan for name in RASTER_FEATURES},
            "raster_status": "SKIPPED_NO_COORDINATE",
            "terrain_status": "SKIPPED_NO_COORDINATE",
        }

    output: dict[str, Any] = {}

    # Solar/Wind/OPTA는 기존처럼 해당 좌표의 단일 픽셀을 읽습니다.
    point_features = [
        name for name in RASTER_FEATURES
        if name not in set(TERRAIN_FEATURE_NAMES)
    ]
    for name in point_features:
        dataset = resources.raster_datasets.get(name)
        if dataset is None:
            output[name] = np.nan
            continue
        try:
            if CRS.from_user_input(dataset.crs) == CRS.from_epsg(4326):
                x, y = float(longitude), float(latitude)
            else:
                transformer = resources.transformers[str(dataset.crs)]
                x, y = transformer.transform(float(longitude), float(latitude))
            if not (dataset.bounds.left <= x <= dataset.bounds.right and dataset.bounds.bottom <= y <= dataset.bounds.top):
                output[name] = np.nan
                continue
            value = next(dataset.sample([(x, y)]))[0]
            if dataset.nodata is not None and np.isclose(value, dataset.nodata, equal_nan=True):
                output[name] = np.nan
            elif not np.isfinite(value):
                output[name] = np.nan
            else:
                output[name] = float(value)
        except Exception:
            output[name] = np.nan

    # 지형은 새 노트북 로직: 3x3 평균 + aspect 원형평균 + Southness 재계산.
    terrain = extract_terrain_features(
        longitude=longitude,
        latitude=latitude,
        datasets=resources.raster_datasets,
        transformers=resources.transformers,
        window_size=settings.terrain_window_size,
        geographic_scale_factor=settings.terrain_scale_factor,
    )
    for name in TERRAIN_FEATURE_NAMES:
        output[name] = terrain.get(name, np.nan)
    output["terrain_status"] = terrain.get("terrain_status", "MISSING")

    output["raster_status"] = (
        "SUCCESS"
        if any(pd.notna(output.get(name)) for name in RASTER_FEATURES)
        else "MISSING"
    )
    return output


def _extract_feature_property(properties: dict[str, Any], aliases: Sequence[str]) -> Any:
    lookup = {str(key).lower(): value for key, value in properties.items()}
    for alias in aliases:
        if alias.lower() in lookup:
            return lookup[alias.lower()]
    return None


def _parse_vworld_wfs_response(response: httpx.Response) -> dict[str, Any]:
    """VWorld WFS의 JSON 또는 parseResponse(JSON) 형식을 해석합니다."""
    response_text = response.text.strip()
    try:
        if response_text.startswith("parseResponse("):
            json_text = response_text[len("parseResponse("):]
            if json_text.endswith(");"):
                json_text = json_text[:-2]
            elif json_text.endswith(")"):
                json_text = json_text[:-1]
            payload = json.loads(json_text)
        else:
            payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"VWorld WFS 응답을 해석할 수 없습니다: {response_text[:300]}") from exc
    if not isinstance(payload, dict) or "features" not in payload:
        raise RuntimeError(f"VWorld WFS 오류 응답: {str(payload)[:300]}")
    return payload


async def query_vworld_parcel(longitude: Any, latitude: Any) -> dict[str, Any]:
    longitude = pd.to_numeric(longitude, errors="coerce")
    latitude = pd.to_numeric(latitude, errors="coerce")
    if pd.isna(longitude) or pd.isna(latitude):
        return {"polygon_status": "SKIPPED_NO_COORDINATE", "polygon_error": None}
    if not settings.vworld_api_key.strip():
        return {"polygon_status": "MISSING_API_KEY", "polygon_error": "VWorld API 키가 없습니다."}
    if resources.http_client is None:
        raise ResourceError("HTTP 클라이언트가 초기화되지 않았습니다.")

    longitude = float(longitude)
    latitude = float(latitude)
    target = Point(longitude, latitude)
    delta = 0.00005
    typenames = list(dict.fromkeys([
        settings.vworld_parcel_typename,
        "lp_pa_cbnd_bonbun",
        "dt_d002",
    ]))
    request_errors: list[str] = []

    for typename in typenames:
        try:
            response = await resources.http_client.get(
                VWORLD_BUILDING_URL,
                params={
                    "key": settings.vworld_api_key,
                    "domain": settings.vworld_domain,
                    "SERVICE": "WFS",
                    "VERSION": "1.1.0",
                    "REQUEST": "GetFeature",
                    "TYPENAME": typename,
                    "OUTPUT": "text/javascript",
                    "SRSNAME": "EPSG:4326",
                    "BBOX": (
                        f"{longitude - delta},{latitude - delta},"
                        f"{longitude + delta},{latitude + delta}"
                    ),
                    "MAXFEATURES": 100,
                },
            )
            response.raise_for_status()
            payload = _parse_vworld_wfs_response(response)

            containing: list[tuple[BaseGeometry, dict[str, Any]]] = []
            for feature in payload.get("features") or []:
                geometry_payload = feature.get("geometry")
                if not geometry_payload:
                    continue
                with suppress(Exception):
                    geometry = shape(geometry_payload)
                    if not geometry.is_empty and geometry.covers(target):
                        containing.append((geometry, feature.get("properties", {}) or {}))

            if not containing:
                continue

            # 경계 중첩 시 가장 작은 필지를 대상 필지로 선택합니다.
            geometry, properties = min(containing, key=lambda item: item[0].area)
            parcel_area = pd.to_numeric(
                _extract_feature_property(
                    properties,
                    ["p_area", "parcel_area", "area", "shape_area", "st_area"],
                ),
                errors="coerce",
            )
            return {
                "polygon_status": "SUCCESS",
                "polygon_error": None,
                "pnu": normalize_text(_extract_feature_property(
                    properties, ["pnu", "pnu_code", "a0", "필지고유번호"]
                )),
                "lot_number": _extract_feature_property(
                    properties, ["jibun", "jibun_addr", "lot_number", "bonbun", "a1"]
                ),
                "land_category": _extract_feature_property(
                    properties, ["jimok", "land_category", "a2"]
                ),
                "parcel_area_m2": float(parcel_area) if pd.notna(parcel_area) else np.nan,
                "geometry_wkt": geometry.wkt,
                "parcel_typename": typename,
            }
        except Exception as exc:
            request_errors.append(f"{typename}:{type(exc).__name__}:{exc}")

    if request_errors and len(request_errors) == len(typenames):
        return {
            "polygon_status": "FAILED",
            "polygon_error": " | ".join(request_errors),
        }
    return {"polygon_status": "NOT_FOUND", "polygon_error": None}


async def query_vworld_buildings(
    longitude: Any,
    latitude: Any,
    target_pnu: Any = None,
    parcel_geometry_wkt: Any = None,
) -> dict[str, Any]:
    """대상 PNU 또는 정확한 주소 좌표와 일치하는 VWorld 건물을 찾습니다."""
    checked_at = now_kst_iso()
    longitude = pd.to_numeric(longitude, errors="coerce")
    latitude = pd.to_numeric(latitude, errors="coerce")
    if pd.isna(longitude) or pd.isna(latitude):
        return {
            "building_status": "SKIPPED_NO_COORDINATE", "building_error": None,
            "candidate_type_api": None, "building_count": 0,
            "building_checked_at": checked_at,
        }
    if not settings.vworld_api_key.strip():
        return {
            "building_status": "MISSING_API_KEY", "building_error": "VWorld API 키가 없습니다.",
            "candidate_type_api": None, "building_count": 0,
            "building_checked_at": checked_at,
        }
    if resources.http_client is None:
        raise ResourceError("HTTP 클라이언트가 초기화되지 않았습니다.")

    longitude = float(longitude)
    latitude = float(latitude)
    target = Point(longitude, latitude)
    target_pnu_text = normalize_text(target_pnu)
    if target_pnu_text is not None:
        target_pnu_text = re.sub(r"\D", "", target_pnu_text)
        if len(target_pnu_text) != 19:
            target_pnu_text = None
    parcel_geometry: BaseGeometry | None = None
    if normalize_text(parcel_geometry_wkt):
        with suppress(Exception):
            parcel_geometry = wkt.loads(str(parcel_geometry_wkt))
            if parcel_geometry.is_empty:
                parcel_geometry = None

    if parcel_geometry is not None:
        minx, miny, maxx, maxy = parcel_geometry.bounds
        padding = 0.00002
        minx, miny, maxx, maxy = minx - padding, miny - padding, maxx + padding, maxy + padding
    else:
        delta = 0.0003
        minx, miny, maxx, maxy = longitude - delta, latitude - delta, longitude + delta, latitude + delta

    try:
        response = await resources.http_client.get(
            VWORLD_BUILDING_URL,
            params={
                "key": settings.vworld_api_key,
                "domain": settings.vworld_domain,
                "SERVICE": "WFS",
                "VERSION": "1.1.0",
                "REQUEST": "GetFeature",
                "TYPENAME": settings.vworld_building_typename,
                "OUTPUT": "text/javascript",
                "SRSNAME": "EPSG:4326",
                "BBOX": f"{minx},{miny},{maxx},{maxy}",
                "MAXFEATURES": 1000,
            },
        )
        response.raise_for_status()
        try:
            response_text = response.text.strip()
            if response_text.startswith("parseResponse("):
                json_text = response_text[len("parseResponse("):]
                if json_text.endswith(");"):
                    json_text = json_text[:-2]
                elif json_text.endswith(")"):
                    json_text = json_text[:-1]
                payload = json.loads(json_text)
            else:
                payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"VWorld 건물 API 응답을 해석할 수 없습니다: {response.text[:300]}") from exc
        if not isinstance(payload, dict) or "features" not in payload:
            raise RuntimeError(f"VWorld 건물 API 오류 응답: {str(payload)[:300]}")

        matched: list[tuple[BaseGeometry, dict[str, Any]]] = []
        for feature in payload.get("features") or []:
            geometry_payload = feature.get("geometry")
            if not geometry_payload:
                continue
            with suppress(Exception):
                geometry = shape(geometry_payload)
                if geometry.is_empty:
                    continue
                properties = feature.get("properties", {}) or {}
                feature_pnu = normalize_text(properties.get("pnu"))
                if feature_pnu is not None:
                    feature_pnu = re.sub(r"\D", "", feature_pnu)

                # 주변에 있는 건물은 제외합니다. 대상 필지 PNU가 같거나,
                # 지오코딩된 정확한 좌표가 건물 폴리곤 내부에 있을 때만 건물입니다.
                pnu_match = bool(target_pnu_text and feature_pnu == target_pnu_text)
                point_match = geometry.covers(target)
                is_match = pnu_match or point_match
                if is_match:
                    matched.append((geometry, properties))

        if not matched:
            return {
                "building_status": "NOT_FOUND", "building_error": None,
                "candidate_type_api": "land", "building_count": 0,
                "main_purpose_names": None, "plat_area_max": np.nan,
                "arch_area_sum": np.nan, "total_area_sum": np.nan,
                "building_checked_at": checked_at,
            }

        purposes = sorted({
            str(value).strip()
            for _, properties in matched
            for value in [_extract_feature_property(
                properties,
                ["bld_nm", "dong_nm", "usability"],
            )]
            if normalize_text(value)
        })

        def matched_numeric(property_name: str) -> list[float]:
            values: list[float] = []
            for _, properties in matched:
                value = pd.to_numeric(properties.get(property_name), errors="coerce")
                if pd.notna(value):
                    values.append(float(value))
            return values

        plat_areas = matched_numeric("platarea")
        arch_areas = matched_numeric("archarea")
        total_areas = matched_numeric("totalarea")
        return {
            "building_status": "SUCCESS", "building_error": None,
            "candidate_type_api": "building", "building_count": len(matched),
            "main_purpose_names": " | ".join(purposes) if purposes else None,
            "plat_area_max": max(plat_areas) if plat_areas else np.nan,
            "arch_area_sum": sum(arch_areas) if arch_areas else np.nan,
            "total_area_sum": sum(total_areas) if total_areas else np.nan,
            "building_checked_at": checked_at,
        }
    except Exception as exc:
        return {
            "building_status": "FAILED", "building_error": f"{type(exc).__name__}: {exc}",
            # 요청한 판정 규칙: 건물을 확인하지 못하면 토지로 분류합니다.
            "candidate_type_api": "land", "building_count": 0,
            "building_checked_at": checked_at,
        }


def pnu_to_building_params(pnu: Any) -> dict[str, str] | None:
    text = normalize_text(pnu)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) != 19:
        return None
    return {
        "sigunguCd": digits[:5],
        "bjdongCd": digits[5:10],
        "platGbCd": "1" if digits[10] == "2" else "0",
        "bun": digits[11:15],
        "ji": digits[15:19],
    }


def _normalize_building_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response", {})
    header = response.get("header", {})
    result_code = str(header.get("resultCode", "00"))
    if result_code not in {"00", "0"}:
        raise RuntimeError(f"{result_code}: {header.get('resultMsg')}")
    items = response.get("body", {}).get("items", {})
    if not items:
        return []
    item = items.get("item", []) if isinstance(items, dict) else items
    if isinstance(item, dict):
        return [item]
    return item if isinstance(item, list) else []


async def fetch_building_register(pnu: Any, polygon_status: str) -> dict[str, Any]:
    checked_at = now_kst_iso()
    if polygon_status != "SUCCESS" or normalize_text(pnu) is None:
        return {
            "building_status": "SKIPPED_NO_VALID_PNU",
            "building_error": "유효한 VWorld PNU가 없어 건축물대장 API를 호출하지 않았습니다.",
            "candidate_type_api": None,
            "building_count": 0,
            "building_checked_at": checked_at,
        }
    if not settings.building_api_key.strip():
        return {
            "building_status": "MISSING_API_KEY",
            "building_error": "공공데이터포털 서비스 키가 없습니다.",
            "candidate_type_api": None,
            "building_count": 0,
            "building_checked_at": checked_at,
        }
    params_from_pnu = pnu_to_building_params(pnu)
    if params_from_pnu is None:
        return {
            "building_status": "INVALID_PNU",
            "building_error": "PNU가 유효한 19자리 형식이 아닙니다.",
            "candidate_type_api": None,
            "building_count": 0,
            "building_checked_at": checked_at,
        }
    if resources.http_client is None:
        raise ResourceError("HTTP 클라이언트가 초기화되지 않았습니다.")

    try:
        response = await resources.http_client.get(
            BUILDING_API_URL,
            params={
                "serviceKey": settings.building_api_key,
                **params_from_pnu,
                "_type": "json",
                "numOfRows": 100,
                "pageNo": 1,
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"건축물대장 API 응답이 JSON이 아닙니다: {response.text[:300]}") from exc
        items = _normalize_building_items(payload)
        if not items:
            return {
                "building_status": "NOT_FOUND",
                "building_error": None,
                "candidate_type_api": "land",
                "building_count": 0,
                "main_purpose_names": None,
                "plat_area_max": np.nan,
                "arch_area_sum": np.nan,
                "total_area_sum": np.nan,
                "building_checked_at": checked_at,
            }

        def numeric_values(key: str) -> list[float]:
            result: list[float] = []
            for item in items:
                value = pd.to_numeric(item.get(key), errors="coerce")
                if pd.notna(value):
                    result.append(float(value))
            return result

        purposes = sorted({str(item.get("mainPurpsCdNm")).strip() for item in items if item.get("mainPurpsCdNm")})
        plat_areas = numeric_values("platArea")
        arch_areas = numeric_values("archArea")
        total_areas = numeric_values("totArea")
        return {
            "building_status": "SUCCESS",
            "building_error": None,
            "candidate_type_api": "building",
            "building_count": len(items),
            "main_purpose_names": " | ".join(purposes) if purposes else None,
            "plat_area_max": max(plat_areas) if plat_areas else np.nan,
            "arch_area_sum": sum(arch_areas) if arch_areas else np.nan,
            "total_area_sum": sum(total_areas) if total_areas else np.nan,
            "building_checked_at": checked_at,
        }
    except Exception as exc:
        return {
            "building_status": "FAILED",
            "building_error": f"{type(exc).__name__}: {exc}",
            "candidate_type_api": None,
            "building_count": 0,
            "building_checked_at": checked_at,
        }


def extract_grid_features(longitude: Any, latitude: Any) -> dict[str, Any]:
    empty = {
        "distance_to_substation_km": np.nan,
        "distance_to_powerline_km": np.nan,
        "substation_count_5km": 0,
        "powerline_length_5km_km": 0.0,
        "high_voltage_line_nearby_5km": 0,
        "substation_max_voltage_kv": np.nan,
        "powerline_max_voltage_kv": np.nan,
        "substation_max_voltage_kv_missing": 1,
        "powerline_max_voltage_kv_missing": 1,
        "grid_status": "MISSING",
    }
    longitude = pd.to_numeric(longitude, errors="coerce")
    latitude = pd.to_numeric(latitude, errors="coerce")
    if pd.isna(longitude) or pd.isna(latitude):
        return {**empty, "grid_status": "SKIPPED_NO_COORDINATE"}
    if resources.substations is None and resources.powerlines is None:
        return {**empty, "grid_status": "MISSING_FILE"}

    try:
        point = gpd.GeoSeries([Point(float(longitude), float(latitude))], crs="EPSG:4326").to_crs(settings.power_grid_projected_crs).iloc[0]
        buffer_area = point.buffer(settings.power_grid_search_radius_m)
        result = dict(empty)

        if resources.substations is not None and not resources.substations.empty:
            result["distance_to_substation_km"] = float(resources.substations.geometry.distance(point).min()) / 1000.0
            indices = list(resources.substations.sindex.query(buffer_area, predicate="intersects"))
            nearby = resources.substations.iloc[indices].copy()
            if not nearby.empty:
                nearby = nearby.loc[nearby.geometry.intersects(buffer_area)].copy()
            result["substation_count_5km"] = int(len(nearby))
            maximum = _gdf_max_voltage_kv(nearby, resources.substation_voltage_columns)
            result["substation_max_voltage_kv"] = maximum
            result["substation_max_voltage_kv_missing"] = int(pd.isna(maximum))

        if resources.powerlines is not None and not resources.powerlines.empty:
            result["distance_to_powerline_km"] = float(resources.powerlines.geometry.distance(point).min()) / 1000.0
            indices = list(resources.powerlines.sindex.query(buffer_area, predicate="intersects"))
            nearby = resources.powerlines.iloc[indices].copy()
            if not nearby.empty:
                nearby = nearby.loc[nearby.geometry.intersects(buffer_area)].copy()
            if not nearby.empty:
                clipped = nearby.copy()
                clipped["geometry"] = clipped.geometry.intersection(buffer_area)
                clipped = clipped.loc[clipped.geometry.notna() & ~clipped.geometry.is_empty]
                result["powerline_length_5km_km"] = float(clipped.geometry.length.sum()) / 1000.0
                maximum = _gdf_max_voltage_kv(nearby, resources.powerline_voltage_columns)
                result["powerline_max_voltage_kv"] = maximum
                result["powerline_max_voltage_kv_missing"] = int(pd.isna(maximum))
                result["high_voltage_line_nearby_5km"] = int(
                    any(
                        pd.notna(value := _row_max_voltage_kv(row, resources.powerline_voltage_columns))
                        and value >= settings.high_voltage_threshold_kv
                        for _, row in nearby.iterrows()
                    )
                )

        result["grid_status"] = "SUCCESS"
        return result
    except Exception as exc:
        return {**empty, "grid_status": f"FAILED: {type(exc).__name__}: {exc}"}


def _processing_status(row: dict[str, Any]) -> str:
    if row.get("geocode_status") != "SUCCESS":
        return "FAILED"

    raster_ok = row.get("raster_status") == "SUCCESS"
    terrain_ok = row.get("terrain_status") == "SUCCESS"
    parcel_ok = row.get("polygon_status") in {"SUCCESS", "NOT_FOUND"}
    building_ok = row.get("building_status") in {"SUCCESS", "NOT_FOUND"}
    grid_ok = row.get("grid_status") == "SUCCESS"
    return "SUCCESS" if raster_ok and terrain_ok and parcel_ok and building_ok and grid_ok else "PARTIAL_SUCCESS"


async def process_one_address(address: str) -> dict[str, Any]:
    """주소 한 건을 최신 원천 데이터로 끝까지 전처리합니다."""
    row: dict[str, Any] = {
        "address_original": address,
        "address_ml": normalize_address(address),
        "updated_at": now_kst_iso(),
        "warning_message": None,
        "error_message": None,
    }

    geocode = await geocode_with_retry(row["address_ml"])
    row.update({key: value for key, value in geocode.items() if key not in {"success", "status"}})
    row["coordinate_status"] = "SUCCESS" if geocode.get("success") else "FAILED_AFTER_RETRY"

    if not geocode.get("success"):
        row.update(extract_raster_features(np.nan, np.nan))
        row.update({"polygon_status": "SKIPPED_NO_COORDINATE", "polygon_error": None})
        row.update(await query_vworld_buildings(np.nan, np.nan))
        row.update(extract_grid_features(np.nan, np.nan))
        row["processing_status"] = "FAILED"
        row["error_message"] = row.get("geocode_error")
        return row

    matched = row.get("matched_address") or row.get("address_ml")
    # address_ml은 사용자가 넣은 지번 주소를 보존하고, 행정구역 보강에는 VWorld 확인 주소를 사용합니다.
    # 이렇게 해야 기존 Merged_Test_Data의 주소 키와 안정적으로 upsert됩니다.
    sido, sigungu = parse_region(matched)
    row["시도"] = sido
    row["시군구"] = sigungu
    row["region_group"] = sido

    row.update(extract_raster_features(row["longitude"], row["latitude"]))
    parcel = await query_vworld_parcel(row["longitude"], row["latitude"])
    row.update(parcel)
    if settings.api_request_interval_seconds:
        await _async_sleep(settings.api_request_interval_seconds)
    row.update(await query_vworld_buildings(
        row.get("longitude"),
        row.get("latitude"),
        row.get("pnu"),
        row.get("geometry_wkt"),
    ))
    if settings.api_request_interval_seconds:
        await _async_sleep(settings.api_request_interval_seconds)
    row.update(extract_grid_features(row["longitude"], row["latitude"]))

    row["processing_status"] = _processing_status(row)
    warnings = []
    for stage, status_key, error_key in [
        ("래스터", "raster_status", None),
        ("지형", "terrain_status", None),
        ("지적도", "polygon_status", "polygon_error"),
        ("VWorld 건물", "building_status", "building_error"),
        ("전력망", "grid_status", None),
    ]:
        status = str(row.get(status_key, ""))
        is_problem = (
            status.startswith("FAILED")
            or status in {"MISSING_API_KEY", "MISSING_FILE"}
            or (stage == "지형" and status != "SUCCESS")
        )
        if is_problem:
            message = f"{stage}:{status}"
            if error_key and row.get(error_key):
                message += f":{row[error_key]}"
            warnings.append(message)
    row["warning_message"] = " | ".join(warnings) if warnings else None
    return row


def _read_existing_table() -> tuple[pd.DataFrame, list[str]]:
    result_path = settings.resolved(settings.result_csv_path)
    base_path = settings.resolved(settings.base_merged_csv_path)

    if result_path.exists():
        existing = pd.read_csv(result_path, encoding="utf-8-sig", low_memory=False)
    elif base_path.exists():
        existing = pd.read_csv(base_path, encoding="utf-8-sig", low_memory=False)
    else:
        raise PipelineError(
            "최초 실행에는 기준 Merged_Test_Data.csv가 필요합니다. "
            f"다음 경로에 넣으세요: {base_path}"
        )

    if existing.empty:
        raise PipelineError("기준 또는 결과 CSV가 비어 있습니다.")
    # 최초 기준 CSV는 ID와 주소만 있어도 됩니다. 나머지 ML 목표 컬럼은
    # 아래에서 자동 생성한 뒤 각 주소의 최신 전처리 결과로 채웁니다.
    required = ["source_id_ml", "address_ml"]
    missing = [column for column in required if column not in existing.columns]
    if missing:
        raise PipelineError(f"기준 CSV에 필수 컬럼이 없습니다: {missing}")

    # 기준/이전 결과 CSV에 없는 목표 컬럼은 자동으로 추가합니다.
    # 값은 아직 처리되지 않았음을 나타내는 NA로 두며, run_pipeline에서
    # 주소별 지오코딩·래스터·지적도·건축물·전력망 결과로 갱신됩니다.
    missing_existing = [column for column in MERGED_OUTPUT_COLUMNS if column not in existing.columns]
    for column in missing_existing:
        existing[column] = pd.NA

    # 기준 CSV가 두 컬럼뿐이어도 기본적으로 확정 가능한 값은 채웁니다.
    if "설치구분" in missing_existing:
        existing["설치구분"] = "미설치"
    if "label" in missing_existing:
        existing["label"] = 0

    base_columns = list(MERGED_OUTPUT_COLUMNS)

    existing["source_id_ml"] = existing["source_id_ml"].astype("string").str.strip()
    existing = existing.drop_duplicates(subset=["source_id_ml"], keep="last").reset_index(drop=True)

    # Merged_Test_Data의 기존 행 수와 source_id를 보존합니다.
    # 같은 주소가 여러 source_id에 존재할 수 있으므로 주소 기준으로 행을 삭제하지 않습니다.
    return existing.reset_index(drop=True), base_columns


def _asset_type_mapping(existing: pd.DataFrame) -> dict[str, float]:
    if "asset_type_code" not in existing.columns:
        return {}
    mapping_df = existing[["자산구분_ML", "asset_type_code"]].copy()
    mapping_df["asset_type_code"] = pd.to_numeric(mapping_df["asset_type_code"], errors="coerce")
    mapping_df = mapping_df.dropna()
    if mapping_df.empty:
        return {"토지": 0.0, "건물": 1.0}
    return mapping_df.groupby("자산구분_ML")["asset_type_code"].agg(lambda values: values.mode().iloc[0]).to_dict()


def _next_solar_id(used_numbers: set[int], width: int = 5) -> str:
    number = 1
    while number in used_numbers:
        number += 1
    used_numbers.add(number)
    return f"SOLAR_{number:0{width}d}"


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _values_equal(left: Any, right: Any) -> bool:
    if _is_missing(left) and _is_missing(right):
        return True
    if isinstance(left, (float, np.floating)) or isinstance(right, (float, np.floating)):
        try:
            return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            pass
    return str(left) == str(right)


def _apply_stage_updates(existing_row: pd.Series | None, current: dict[str, Any]) -> dict[str, Any]:
    """조회 실패 시 기존 정상값을 보존하고, 정상 조회값은 최신값으로 교체합니다."""
    result = {} if existing_row is None else existing_row.to_dict()

    # 입력·상태·시간 정보는 항상 이번 실행값으로 갱신합니다.
    always_columns = ["address_original", "updated_at", *STATUS_COLUMNS]
    for column in always_columns:
        if column in current:
            result[column] = current[column]

    geocode_ok = current.get("geocode_status") == "SUCCESS"
    raster_ok = current.get("raster_status") == "SUCCESS"
    parcel_status = str(current.get("polygon_status"))
    building_status = str(current.get("building_status"))
    grid_ok = str(current.get("grid_status")) == "SUCCESS"

    if existing_row is None or geocode_ok:
        for column in GEOCODE_UPDATE_COLUMNS:
            if column in current:
                result[column] = current[column]

    if existing_row is None:
        for column in RASTER_FEATURES:
            result[column] = current.get(column, np.nan)
    elif raster_ok:
        # 일부 래스터만 누락된 재처리에서는 기존 정상값을 NaN으로 덮지 않습니다.
        for column in RASTER_FEATURES:
            value = current.get(column, np.nan)
            if not _is_missing(value):
                result[column] = value

    # NOT_FOUND는 정상 조회 결과이므로 기존 지적 속성을 비웁니다.
    if existing_row is None or parcel_status in {"SUCCESS", "NOT_FOUND"}:
        for column in PARCEL_COLUMNS:
            result[column] = current.get(column, np.nan)

    # NOT_FOUND는 정상적으로 토지로 판정된 결과입니다.
    if existing_row is None or building_status in {"SUCCESS", "NOT_FOUND"}:
        for column in BUILDING_COLUMNS:
            result[column] = current.get(column, np.nan)

    if existing_row is None or grid_ok:
        for column in GRID_FEATURES:
            result[column] = current.get(column, np.nan)

    # 새 후보는 현재 가지고 있는 보조값을 모두 담습니다.
    if existing_row is None:
        for column, value in current.items():
            if column != "geometry_wkt":
                result[column] = value

    return result


def _build_upsert_result(current_rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[str], list[dict[str, Any]], dict[str, int]]:
    existing, base_columns = _read_existing_table()
    existing["_address_key"] = existing["address_ml"].map(normalize_address_key)
    address_to_index = {
        key: index
        for index, key in existing["_address_key"].items()
        if key is not None and not _is_missing(key)
    }

    number_text = existing["source_id_ml"].astype("string").str.extract(r"^SOLAR_(\d+)$", expand=False).dropna()
    used_numbers = set(number_text.astype(int).tolist())
    width = max(5, int(number_text.str.len().max())) if not number_text.empty else 5
    changed_records: list[dict[str, Any]] = []
    polygon_actions: list[dict[str, Any]] = []
    summary = {"new": 0, "updated": 0, "unchanged": 0, "partial": 0, "failed": 0}

    for current in current_rows:
        key = normalize_address_key(current.get("address_ml"))
        existing_index = address_to_index.get(key) if key else None
        old_row = existing.loc[existing_index] if existing_index is not None else None
        source_id = normalize_text(old_row.get("source_id_ml")) if old_row is not None else None
        if source_id is None or re.fullmatch(r"SOLAR_\d+", source_id) is None:
            source_id = _next_solar_id(used_numbers, width)

        updated = _apply_stage_updates(old_row, current)
        updated["source_id_ml"] = source_id
        updated["candidate_id"] = source_id
        updated["address_ml"] = current.get("address_ml") or updated.get("address_ml")
        updated["설치구분"] = updated.get("설치구분") if not _is_missing(updated.get("설치구분")) else "미설치"
        updated["label"] = updated.get("label") if not _is_missing(updated.get("label")) else 0

        # 기준 CSV의 기존 값/매핑에 의존하지 않고 이번 VWorld 판정을 직접 반영합니다.
        # 건물을 정확히 찾은 경우만 building이며, 그 외에는 요청 규칙대로 land입니다.
        candidate_type = normalize_text(current.get("candidate_type_api"))
        if candidate_type != "building":
            candidate_type = "land"
        updated["candidate_type_api"] = candidate_type
        updated["자산구분_ML"] = "건물" if candidate_type == "building" else "토지"
        updated["asset_type_code"] = 1 if candidate_type == "building" else 0

        partial = current.get("processing_status") == "PARTIAL_SUCCESS" or (
            old_row is not None and current.get("geocode_status") != "SUCCESS"
        )
        if old_row is None:
            change_status = "FAILED" if current.get("processing_status") == "FAILED" else "NEW"
        else:
            compare_columns = [
                column
                for column in set(GEOCODE_UPDATE_COLUMNS + RASTER_FEATURES + PARCEL_COLUMNS + BUILDING_COLUMNS + GRID_FEATURES + ["자산구분_ML", "asset_type_code"])
                if column in updated or column in old_row.index
            ]
            changed = any(not _values_equal(old_row.get(column), updated.get(column)) for column in compare_columns)
            change_status = "UPDATE_PARTIAL" if partial else ("UPDATED" if changed else "UNCHANGED")

        updated["change_status"] = change_status
        if change_status == "NEW":
            summary["new"] += 1
        elif change_status == "UPDATED":
            summary["updated"] += 1
        elif change_status == "UNCHANGED":
            summary["unchanged"] += 1
        elif change_status == "UPDATE_PARTIAL":
            summary["partial"] += 1
        else:
            summary["failed"] += 1

        if old_row is None:
            existing = pd.concat([existing, pd.DataFrame([updated])], ignore_index=True, sort=False)
            new_index = existing.index[-1]
            if key:
                address_to_index[key] = new_index
        else:
            for column, value in updated.items():
                existing.loc[existing_index, column] = value

        changed_records.append(updated)
        polygon_actions.append(
            {
                "source_id_ml": source_id,
                "address_ml": updated.get("address_ml"),
                "polygon_status": current.get("polygon_status"),
                "pnu": current.get("pnu"),
                "lot_number": current.get("lot_number"),
                "land_category": current.get("land_category"),
                "parcel_area_m2": current.get("parcel_area_m2"),
                "geometry_wkt": current.get("geometry_wkt"),
                "updated_at": current.get("updated_at"),
            }
        )

    existing = existing.drop(columns=["_address_key"], errors="ignore")
    final_columns = list(MERGED_OUTPUT_COLUMNS)
    for column in final_columns:
        if column not in existing.columns:
            existing[column] = pd.NA
    existing = existing[final_columns]
    existing = existing.drop_duplicates(subset=["source_id_ml"], keep="last").reset_index(drop=True)
    return existing, final_columns, changed_records, summary


def _atomic_write_csv(data: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", dir=destination.parent, delete=False, encoding="utf-8-sig", newline="") as temp:
        temp_path = Path(temp.name)
    try:
        data.to_csv(temp_path, index=False, encoding="utf-8-sig")
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def _parse_polygon_wkt(value: Any) -> BaseGeometry | None:
    text = normalize_text(value)
    if text is None:
        return None
    try:
        geometry = wkt.loads(text)
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            return None
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        return None if geometry.is_empty else geometry
    except Exception:
        return None


def _load_existing_polygons() -> gpd.GeoDataFrame:
    gpkg_path = settings.resolved(settings.parcel_gpkg_path)
    if not gpkg_path.exists():
        return gpd.GeoDataFrame(columns=["source_id_ml", "geometry"], geometry="geometry", crs="EPSG:4326")
    try:
        result = gpd.read_file(gpkg_path, layer=settings.parcel_gpkg_layer)
        if result.crs is None:
            result = result.set_crs("EPSG:4326")
        return result.to_crs("EPSG:4326")
    except Exception:
        return gpd.GeoDataFrame(columns=["source_id_ml", "geometry"], geometry="geometry", crs="EPSG:4326")


def _save_polygons(actions: list[dict[str, Any]]) -> None:
    existing = _load_existing_polygons()
    if "source_id_ml" not in existing.columns:
        existing["source_id_ml"] = pd.NA
    existing["source_id_ml"] = existing["source_id_ml"].astype("string").str.strip()

    remove_ids: set[str] = set()
    new_rows: list[dict[str, Any]] = []
    for action in actions:
        source_id = str(action["source_id_ml"])
        status = str(action.get("polygon_status"))
        if status == "SUCCESS":
            geometry = _parse_polygon_wkt(action.get("geometry_wkt"))
            if geometry is not None:
                remove_ids.add(source_id)
                new_rows.append(
                    {
                        "source_id_ml": source_id,
                        "candidate_id": source_id,
                        "candidate_type": action.get("candidate_type"),
                        "candidate_area_m2": action.get("parcel_area_m2"),
                        "address": action.get("address_ml"),
                        "address_ml": action.get("address_ml"),
                        "pnu": action.get("pnu"),
                        "lot_number": action.get("lot_number"),
                        "land_category": action.get("land_category"),
                        "parcel_area_m2": action.get("parcel_area_m2"),
                        "updated_at": action.get("updated_at"),
                        "geometry": geometry,
                    }
                )
        elif status == "NOT_FOUND":
            # 정상적으로 필지를 찾지 못한 최신 결과이면 과거 Polygon을 제거합니다.
            remove_ids.add(source_id)

    if remove_ids:
        existing = existing.loc[~existing["source_id_ml"].astype(str).isin(remove_ids)].copy()
    current = gpd.GeoDataFrame(new_rows, geometry="geometry", crs="EPSG:4326") if new_rows else gpd.GeoDataFrame(columns=existing.columns, geometry="geometry", crs="EPSG:4326")
    combined = pd.concat([existing, current], ignore_index=True, sort=False)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    combined = combined.drop_duplicates(subset=["source_id_ml"], keep="last").reset_index(drop=True)
    if combined.empty:
        return

    for column in combined.columns:
        if column == "geometry":
            continue
        combined[column] = combined[column].map(
            lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, tuple, set)) else value
        )

    geojson_path = settings.resolved(settings.parcel_geojson_path)
    gpkg_path = settings.resolved(settings.parcel_gpkg_path)
    geojson_path.parent.mkdir(parents=True, exist_ok=True)
    temp_geojson = geojson_path.with_name(geojson_path.stem + ".tmp.geojson")
    temp_gpkg = gpkg_path.with_name(gpkg_path.stem + ".tmp.gpkg")
    temp_geojson.unlink(missing_ok=True)
    temp_gpkg.unlink(missing_ok=True)
    combined.to_file(temp_geojson, driver="GeoJSON", encoding="utf-8")
    combined.to_file(temp_gpkg, layer=settings.parcel_gpkg_layer, driver="GPKG")
    os.replace(temp_geojson, geojson_path)
    os.replace(temp_gpkg, gpkg_path)


async def _fetch_vworld_vision_image(
    longitude: Any,
    latitude: Any,
) -> tuple[bytes, str, list[float]]:
    """VWorld 항공영상과 영상에 정확히 대응하는 EPSG:3857 extent를 반환합니다."""
    longitude = float(pd.to_numeric(longitude, errors="raise"))
    latitude = float(pd.to_numeric(latitude, errors="raise"))
    if resources.http_client is None:
        raise ResourceError("HTTP 클라이언트가 초기화되지 않았습니다.")

    size = settings.vision_image_size
    zoom = settings.vision_image_zoom
    response = await resources.http_client.get(
        VWORLD_IMAGE_URL,
        params={
            "service": "image",
            "request": "getmap",
            "version": "2.0",
            "crs": "EPSG:4326",
            "center": f"{longitude},{latitude}",
            "zoom": zoom,
            "size": f"{size},{size}",
            "format": "png",
            "basemap": "PHOTO",
            "key": settings.vworld_api_key,
            "domain": settings.vworld_domain,
        },
        timeout=settings.vision_timeout_seconds,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    if not content_type.startswith("image/"):
        raise RuntimeError(f"VWorld 영상 응답이 이미지가 아닙니다: {response.text[:300]}")

    centre_x, centre_y = Transformer.from_crs(4326, 3857, always_xy=True).transform(
        longitude, latitude
    )
    resolution = (2.0 * math.pi * 6378137.0) / (256.0 * (2 ** zoom))
    half_span = resolution * size / 2.0
    extent = [
        centre_x - half_span, centre_y - half_span,
        centre_x + half_span, centre_y + half_span,
    ]
    return response.content, content_type, extent


async def _run_vision_for_record(record: dict[str, Any]) -> dict[str, Any]:
    source_id = normalize_text(record.get("source_id_ml"))
    output: dict[str, Any] = {
        "source_id_ml": source_id,
        "address_ml": record.get("address_ml"),
        **{column: None for column in VISION_COLUMNS},
        "vision_status": "SKIPPED",
        "vision_error": None,
        "vision_model_version": None,
    }
    if not settings.vision_enabled:
        output["vision_error"] = "VISION_ENABLED=false"
        return output
    if normalize_text(record.get("polygon_status")) != "SUCCESS":
        output["vision_error"] = "필지 폴리곤이 없어 Vision을 실행하지 않았습니다."
        return output
    if resources.http_client is None:
        output.update(vision_status="FAILED", vision_error="HTTP 클라이언트가 없습니다.")
        return output

    try:
        image_bytes, content_type, extent = await _fetch_vworld_vision_image(
            record.get("longitude"), record.get("latitude")
        )
        response = await resources.http_client.post(
            settings.vision_api_url,
            data={
                "extent3857": json.dumps(extent),
                "candidate_id": source_id or "",
            },
            files={"image": ("vworld.png", image_bytes, content_type)},
            timeout=settings.vision_timeout_seconds,
        )
        if response.is_error:
            try:
                error_payload = response.json()
                error_detail = error_payload.get("detail", error_payload)
            except (ValueError, TypeError, AttributeError):
                error_detail = response.text[:2000]
            raise RuntimeError(
                f"Vision API 오류 ({response.status_code}): {error_detail}"
            )
        payload = response.json()
        predictions = payload.get("predictions") or []
        if not predictions:
            output.update(
                vision_status="NOT_FOUND",
                vision_error="Vision 응답에 후보지 prediction이 없습니다.",
            )
            return output

        prediction = next(
            (
                item for item in predictions
                if normalize_text(item.get("candidate_id")) == source_id
            ),
            predictions[0],
        )
        for column in VISION_COLUMNS:
            output[column] = prediction.get(column)
        output["candidate_id"] = prediction.get("candidate_id") or source_id
        output["pnu"] = prediction.get("pnu") or record.get("pnu")
        output["vision_status"] = "SUCCESS"
        output["vision_model_version"] = prediction.get("model_version")
        return output
    except Exception as exc:
        output.update(
            vision_status="FAILED",
            vision_error=f"{type(exc).__name__}: {exc}",
        )
        return output


async def _run_vision_for_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [await _run_vision_for_record(record) for record in records]


def _save_vision_outputs(
    merged_records: list[dict[str, Any]],
    vision_records: list[dict[str, Any]],
) -> None:
    vision_path = settings.resolved(settings.vision_features_csv_path)
    combined_path = settings.resolved(settings.vision_combined_csv_path)
    columns = ["source_id_ml", "address_ml", *VISION_COLUMNS, *VISION_AUDIT_COLUMNS]
    latest_vision = pd.DataFrame(vision_records, columns=columns)

    if vision_path.exists():
        try:
            existing = pd.read_csv(vision_path, encoding="utf-8-sig")
        except Exception:
            existing = pd.DataFrame(columns=columns)
        vision_all = pd.concat([existing, latest_vision], ignore_index=True, sort=False)
    else:
        vision_all = latest_vision.copy()
    vision_all = vision_all.drop_duplicates(subset=["source_id_ml"], keep="last")
    for column in columns:
        if column not in vision_all.columns:
            vision_all[column] = pd.NA
    _atomic_write_csv(vision_all[columns], vision_path)

    latest_merged = pd.DataFrame(
        _project_merged_records(merged_records), columns=MERGED_OUTPUT_COLUMNS
    )
    combined = latest_merged.merge(
        latest_vision.drop(columns=["address_ml"], errors="ignore"),
        on="source_id_ml",
        how="left",
    )
    _atomic_write_csv(combined, combined_path)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _project_merged_record(record: dict[str, Any]) -> dict[str, Any]:
    """내부 상태값을 제거하고 ML이 사용하는 34개 컬럼만 정확한 순서로 반환합니다."""
    return {column: record.get(column, np.nan) for column in MERGED_OUTPUT_COLUMNS}


def _project_merged_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_project_merged_record(record) for record in records]


def _missing_merged_values(record: dict[str, Any]) -> list[str]:
    """ML용 행이 완성됐는지 검사합니다.

    변전소/전력선 최대전압은 원본 OSM에 voltage 태그가 없을 수 있으므로
    corresponding *_missing == 1이면 NaN을 정상적인 '관측 불가' 값으로 허용합니다.
    """
    missing: list[str] = []
    for column in MERGED_OUTPUT_COLUMNS:
        value = record.get(column, np.nan)
        if not _is_missing(value):
            continue
        flag_column = CONDITIONAL_MISSING_COLUMNS.get(column)
        if flag_column is not None:
            flag = pd.to_numeric(record.get(flag_column), errors="coerce")
            if pd.notna(flag) and int(flag) == 1:
                continue
        missing.append(column)
    return missing


def _audit_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audit_columns = [
        "source_id_ml", "address_ml", "processing_status", "change_status",
        "geocode_status", "raster_status", "terrain_status", "polygon_status",
        "building_status", "grid_status", "warning_message", "error_message",
    ]
    output: list[dict[str, Any]] = []
    for record in records:
        missing = _missing_merged_values(record)
        row = {column: record.get(column) for column in audit_columns}
        row["merged_row_complete"] = len(missing) == 0
        row["missing_merged_columns"] = " | ".join(missing) if missing else None
        output.append(row)
    return output


def _safe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {column: _json_safe(value) for column, value in _project_merged_record(record).items()}
        for record in records
    ]


async def run_pipeline(addresses: Iterable[Any], *, save: bool = True) -> dict[str, Any]:
    prepared = prepare_addresses(addresses)
    current_rows: list[dict[str, Any]] = []
    for address in prepared:
        current_rows.append(await process_one_address(address))

    result_path = settings.resolved(settings.result_csv_path)
    latest_path = settings.resolved(settings.latest_rows_csv_path)
    audit_path = settings.resolved(settings.audit_csv_path)
    lock_path = result_path.with_suffix(result_path.suffix + ".lock")

    if save:
        with FileLock(str(lock_path), timeout=120):
            merged, _, changed_records, change_summary = _build_upsert_result(current_rows)
            incomplete = [
                {
                    "address": record.get("address_ml"),
                    "missing": _missing_merged_values(record),
                }
                for record in changed_records
                if _missing_merged_values(record)
            ]
            if settings.require_complete_merged_rows and incomplete:
                detail = "; ".join(
                    f"{item['address']}: {', '.join(item['missing'])}"
                    for item in incomplete
                )
                raise PipelineError(
                    "완전한 Merged CSV 행을 만들지 못해 저장을 중단했습니다. "
                    f"누락 컬럼 -> {detail}"
                )

            # 최종 ML 데이터셋: Merged_Test_Data(8).csv와 동일한 34개 컬럼만 저장합니다.
            merged = merged[MERGED_OUTPUT_COLUMNS].copy()
            latest = pd.DataFrame(_project_merged_records(changed_records), columns=MERGED_OUTPUT_COLUMNS)
            audit = pd.DataFrame(_audit_records(changed_records))
            _atomic_write_csv(merged, result_path)
            _atomic_write_csv(latest, latest_path)
            _atomic_write_csv(audit, audit_path)

            polygon_actions: list[dict[str, Any]] = []
            for current_row, changed_row in zip(current_rows, changed_records):
                polygon_actions.append(
                    {
                        "source_id_ml": changed_row.get("source_id_ml"),
                        "candidate_type": (
                            "building"
                            if normalize_text(changed_row.get("자산구분_ML")) == "건물"
                            else "land"
                        ),
                        "address_ml": current_row.get("address_ml"),
                        "polygon_status": current_row.get("polygon_status"),
                        "pnu": current_row.get("pnu"),
                        "lot_number": current_row.get("lot_number"),
                        "land_category": current_row.get("land_category"),
                        "parcel_area_m2": current_row.get("parcel_area_m2"),
                        "geometry_wkt": current_row.get("geometry_wkt"),
                        "updated_at": current_row.get("updated_at"),
                    }
                )
            _save_polygons(polygon_actions)
    else:
        _, _, changed_records, change_summary = _build_upsert_result(current_rows)
        incomplete = [record for record in changed_records if _missing_merged_values(record)]

    vision_records: list[dict[str, Any]] = []
    if save:
        vision_records = await _run_vision_for_records(changed_records)
        _save_vision_outputs(changed_records, vision_records)

    status_counts = pd.Series([row.get("processing_status") for row in current_rows]).value_counts(dropna=False).to_dict()
    vision_status_counts = (
        pd.Series([row.get("vision_status") for row in vision_records])
        .value_counts(dropna=False)
        .to_dict()
        if vision_records else {}
    )
    complete_count = sum(not _missing_merged_values(record) for record in changed_records)
    return {
        "success": True,
        "save": save,
        "summary": {
            "received": len(prepared),
            "unique_addresses": len(prepared),
            "merged_columns": len(MERGED_OUTPUT_COLUMNS),
            "complete_rows": complete_count,
            "incomplete_rows": len(changed_records) - complete_count,
            "processing_status": {str(key): int(value) for key, value in status_counts.items()},
            "vision_status": {str(key): int(value) for key, value in vision_status_counts.items()},
            "changes": change_summary,
        },
        "result_files": {
            "merged_csv": str(result_path) if save else None,
            "latest_rows_csv": str(latest_path) if save else None,
            "audit_csv": str(audit_path) if save else None,
            "geojson": str(settings.resolved(settings.parcel_geojson_path)) if save else None,
            "gpkg": str(settings.resolved(settings.parcel_gpkg_path)) if save else None,
            "vision_features_csv": str(settings.resolved(settings.vision_features_csv_path)) if save else None,
            "latest_rows_with_vision_csv": str(settings.resolved(settings.vision_combined_csv_path)) if save else None,
        },
        "results": _safe_records(changed_records),
        "vision_results": [
            {key: _json_safe(value) for key, value in row.items()}
            for row in vision_records
        ],
        "audit": [
            {key: _json_safe(value) for key, value in row.items()}
            for row in _audit_records(changed_records)
        ],
    }
