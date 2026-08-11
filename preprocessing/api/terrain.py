from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import CRS, Transformer


TERRAIN_FEATURE_NAMES = (
    "elevation_avg",
    "slope_avg",
    "slope_dir",
    "Hillshade",
    "Southness",
)


def prepare_window(
    array: np.ndarray,
    *,
    nodata: float | int | None = None,
    valid_min: float | None = None,
    valid_max: float | None = None,
) -> np.ndarray:
    """NoData/비정상 값을 NaN으로 바꿔 평균 계산에 사용합니다."""
    result = array.astype("float64", copy=True)

    if nodata is not None:
        result[np.isclose(result, nodata, equal_nan=True)] = np.nan

    result[~np.isfinite(result)] = np.nan

    if valid_min is not None:
        result[result < valid_min] = np.nan
    if valid_max is not None:
        result[result > valid_max] = np.nan

    return result


def arithmetic_mean(array: np.ndarray) -> float:
    """DEM, slope, hillshade처럼 일반 수치형 래스터의 평균입니다."""
    if array.size == 0 or np.all(np.isnan(array)):
        return np.nan
    return float(np.nanmean(array))


def circular_aspect_mean(array: np.ndarray) -> float:
    """경사향의 원형평균. 359°와 1°는 0°에 가깝게 평균됩니다."""
    values = array[np.isfinite(array)]
    if values.size == 0:
        return np.nan

    radians = np.deg2rad(values)
    mean_sin = float(np.mean(np.sin(radians)))
    mean_cos = float(np.mean(np.cos(radians)))
    vector_strength = float(np.hypot(mean_sin, mean_cos))

    if vector_strength < 1e-8:
        return np.nan

    normalized = float(np.degrees(np.arctan2(mean_sin, mean_cos)) % 360.0)
    # 부동소수점 오차로 0°가 360°에 매우 가깝게 나오는 경우를 0°로 정규화합니다.
    if np.isclose(normalized, 360.0, atol=1e-10):
        return 0.0
    return normalized


def _to_dataset_xy(
    dataset: rasterio.io.DatasetReader,
    longitude: float,
    latitude: float,
    transformers: dict[str, Transformer],
) -> tuple[float, float]:
    if dataset.crs is None:
        raise ValueError(f"래스터 CRS가 없습니다: {dataset.name}")

    if CRS.from_user_input(dataset.crs) == CRS.from_epsg(4326):
        return longitude, latitude

    crs_key = str(dataset.crs)
    transformer = transformers.get(crs_key)
    if transformer is None:
        transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        transformers[crs_key] = transformer

    return transformer.transform(longitude, latitude)


def _read_window(
    dataset: rasterio.io.DatasetReader,
    x: float,
    y: float,
    *,
    window_size: int,
    valid_min: float | None = None,
    valid_max: float | None = None,
) -> np.ndarray:
    if not (
        dataset.bounds.left <= x <= dataset.bounds.right
        and dataset.bounds.bottom <= y <= dataset.bounds.top
    ):
        return np.array([], dtype="float64")

    row, col = dataset.index(x, y)
    half = window_size // 2

    row_start = max(row - half, 0)
    row_end = min(row + half + 1, dataset.height)
    col_start = max(col - half, 0)
    col_end = min(col + half + 1, dataset.width)

    array = dataset.read(
        1,
        window=((row_start, row_end), (col_start, col_end)),
        boundless=False,
    )
    return prepare_window(
        array,
        nodata=dataset.nodata,
        valid_min=valid_min,
        valid_max=valid_max,
    )


def _derive_from_dem(
    dem: rasterio.io.DatasetReader,
    x: float,
    y: float,
    *,
    window_size: int,
    geographic_scale_factor: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """DEM의 작은 윈도우에서 고도·경사도·경사향·음영을 즉석 계산합니다.

    중앙 N×N 결과를 안정적으로 계산하기 위해 DEM은 가장자리 한 픽셀을
    추가한 (N+2)×(N+2)만 읽습니다. 전체 래스터는 메모리에 올리지 않습니다.
    """
    dem_window = _read_window(dem, x, y, window_size=window_size + 2)
    if dem_window.shape[0] < 3 or dem_window.shape[1] < 3:
        empty = np.array([], dtype="float64")
        return empty, empty, empty, empty

    # 주변 NoData가 일부 있어도 계산 가능한 픽셀은 살립니다.
    if np.isnan(dem_window).any():
        fill = float(np.nanmean(dem_window)) if not np.all(np.isnan(dem_window)) else np.nan
        dem_window = np.where(np.isnan(dem_window), fill, dem_window)
    if not np.isfinite(dem_window).any():
        empty = np.array([], dtype="float64")
        return empty, empty, empty, empty

    pixel_x = abs(float(dem.transform.a))
    pixel_y = abs(float(dem.transform.e))
    crs = CRS.from_user_input(dem.crs)
    if crs.is_geographic:
        # 기존 gdaldem -s 설정과 동일한 축척을 사용해 모델 입력 분포를 유지합니다.
        pixel_x *= geographic_scale_factor
        pixel_y *= geographic_scale_factor
    else:
        # 투영좌표계 단위를 metre로 변환합니다(대부분 conversion_factor=1).
        axis = crs.axis_info
        factor = float(axis[0].unit_conversion_factor) if axis else 1.0
        pixel_x *= factor
        pixel_y *= factor

    if pixel_x <= 0 or pixel_y <= 0:
        raise ValueError("DEM 픽셀 크기가 올바르지 않습니다.")

    # 행 방향은 남쪽으로 증가하므로 북쪽 방향 고도 변화량의 부호를 반전합니다.
    dz_drow, dz_dx = np.gradient(dem_window, pixel_y, pixel_x)
    dz_dnorth = -dz_drow
    slope_rad = np.arctan(np.hypot(dz_dx, dz_dnorth))
    slope_deg = np.degrees(slope_rad)

    # 최대 하강 방향을 북쪽=0°, 시계방향 방위각으로 표현합니다.
    aspect_deg = np.degrees(np.arctan2(-dz_dx, -dz_dnorth)) % 360.0
    aspect_deg[np.hypot(dz_dx, dz_dnorth) < 1e-12] = np.nan

    azimuth_rad = np.deg2rad(315.0)
    altitude_rad = np.deg2rad(45.0)
    aspect_rad = np.deg2rad(aspect_deg)
    hillshade = 255.0 * (
        np.sin(altitude_rad) * np.cos(slope_rad)
        + np.cos(altitude_rad) * np.sin(slope_rad) * np.cos(azimuth_rad - aspect_rad)
    )
    hillshade = np.clip(hillshade, 0.0, 255.0)
    hillshade[~np.isfinite(aspect_deg)] = 255.0 * np.sin(altitude_rad)

    # 추가로 읽은 한 픽셀 테두리를 제외해 요청한 N×N만 평균에 사용합니다.
    return (
        dem_window[1:-1, 1:-1],
        slope_deg[1:-1, 1:-1],
        aspect_deg[1:-1, 1:-1],
        hillshade[1:-1, 1:-1],
    )


def _aligned_with_reference(
    reference: rasterio.io.DatasetReader,
    other: rasterio.io.DatasetReader,
) -> bool:
    return (
        other.shape == reference.shape
        and other.transform == reference.transform
        and other.crs == reference.crs
    )


def extract_terrain_features(
    *,
    longitude: Any,
    latitude: Any,
    datasets: dict[str, rasterio.io.DatasetReader],
    transformers: dict[str, Transformer],
    window_size: int = 3,
    geographic_scale_factor: float = 111120.0,
) -> dict[str, Any]:
    """
    업로드 노트북의 지형 로직을 FastAPI용으로 옮긴 함수입니다.

    - DEM, slope, aspect, hillshade를 동일 위치의 N×N 윈도우로 읽음
    - elevation/slope/hillshade는 산술평균
    - aspect는 원형평균
    - Southness = -cos(mean_aspect)
    """
    empty = {
        "elevation_avg": np.nan,
        "slope_avg": np.nan,
        "slope_dir": np.nan,
        "Hillshade": np.nan,
        "Southness": np.nan,
        "terrain_status": "MISSING",
    }

    if window_size < 1 or window_size % 2 == 0:
        raise ValueError("terrain_window_size는 1 이상의 홀수여야 합니다.")

    try:
        lon = float(longitude)
        lat = float(latitude)
    except (TypeError, ValueError):
        return {**empty, "terrain_status": "SKIPPED_NO_COORDINATE"}

    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return {**empty, "terrain_status": "INVALID_COORDINATE"}

    dem = datasets.get("elevation_avg")
    slope = datasets.get("slope_avg")
    aspect = datasets.get("slope_dir")
    hillshade = datasets.get("Hillshade")

    if dem is None:
        return {**empty, "terrain_status": "MISSING_DEM"}

    # 노트북과 동일하게 파생 래스터가 DEM과 같은 격자인지 확인합니다.
    for name, dataset in (
        ("slope", slope),
        ("aspect", aspect),
        ("hillshade", hillshade),
    ):
        if dataset is not None and not _aligned_with_reference(dem, dataset):
            return {**empty, "terrain_status": f"GRID_MISMATCH_{name.upper()}"}

    try:
        x, y = _to_dataset_xy(dem, lon, lat, transformers)

        if slope is None or aspect is None or hillshade is None:
            dem_window, derived_slope, derived_aspect, derived_hillshade = _derive_from_dem(
                dem,
                x,
                y,
                window_size=window_size,
                geographic_scale_factor=geographic_scale_factor,
            )
        else:
            dem_window = _read_window(dem, x, y, window_size=window_size)
            derived_slope = derived_aspect = derived_hillshade = np.array([], dtype="float64")

        slope_window = (
            _read_window(slope, x, y, window_size=window_size, valid_min=0, valid_max=90)
            if slope is not None
            else derived_slope
        )
        aspect_window = (
            _read_window(aspect, x, y, window_size=window_size, valid_min=0, valid_max=360)
            if aspect is not None
            else derived_aspect
        )
        hill_window = (
            _read_window(hillshade, x, y, window_size=window_size, valid_min=0, valid_max=255)
            if hillshade is not None
            else derived_hillshade
        )

        mean_elevation = arithmetic_mean(dem_window)
        mean_slope = arithmetic_mean(slope_window)
        mean_aspect = circular_aspect_mean(aspect_window)
        mean_hillshade = arithmetic_mean(hill_window)

        southness = (
            float(-np.cos(np.deg2rad(mean_aspect)))
            if np.isfinite(mean_aspect)
            else np.nan
        )

        values = {
            "elevation_avg": round(mean_elevation, 3) if np.isfinite(mean_elevation) else np.nan,
            "slope_avg": round(mean_slope, 3) if np.isfinite(mean_slope) else np.nan,
            "slope_dir": round(mean_aspect, 3) if np.isfinite(mean_aspect) else np.nan,
            "Hillshade": round(mean_hillshade, 3) if np.isfinite(mean_hillshade) else np.nan,
            "Southness": round(southness, 5) if np.isfinite(southness) else np.nan,
        }

        if all(not np.isfinite(value) for value in values.values()):
            return {**values, "terrain_status": "OUTSIDE_RASTER_OR_NODATA"}

        missing = [name for name, value in values.items() if not np.isfinite(value)]
        status = "SUCCESS" if not missing else "PARTIAL:" + ",".join(missing)
        return {**values, "terrain_status": status}

    except Exception as exc:
        return {
            **empty,
            "terrain_status": f"FAILED:{type(exc).__name__}:{str(exc)[:120]}",
        }
