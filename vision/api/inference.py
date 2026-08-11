from __future__ import annotations

import base64
from datetime import datetime
from dataclasses import dataclass
import os
from pathlib import Path
from threading import Lock
from typing import Any

import cv2
import numpy as np
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from ultralytics import YOLO

from .config import Settings
from .gpkg_candidates import CandidateParcelRepository


INSTALLABLE = {"building", "parking_lot", "land"}
PANEL_WIDTH_M, PANEL_HEIGHT_M = 2.465, 1.134
PANEL_GAP_M, EDGE_MARGIN_M = 0.2, 0.4
ROAD_CLEARANCE_PX, BUILDING_CLEARANCE_PX = 20.0, 10.0
# VWorld Building Polygon과 항공영상 사이의 위치 오차를 보정하기 위한 Spatial Matching.
SPATIAL_TOLERANCE_M = 5.0
DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"
FINAL_IMAGE_SIZE = (480, 408)
FINAL_IMAGE_JPEG_QUALITY = 80


@dataclass(frozen=True)
class Extent3857:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def validate(self) -> None:
        if self.min_x >= self.max_x or self.min_y >= self.max_y:
            raise ValueError("extent3857 must satisfy minX < maxX and minY < maxY.")


class YoloSegmentationService:
    """One GPKG parcel per request; YOLO analyses only its environment."""

    def __init__(self, settings: Settings) -> None:
        if not settings.model_path.is_file():
            raise RuntimeError(f"YOLO model file does not exist: {settings.model_path}")
        self._settings = settings
        self._model = YOLO(str(settings.model_path))
        self._predict_lock = Lock()

    def predict(
        self,
        image: np.ndarray,
        extent: Extent3857,
        candidate_id: str | None = None,
    ) -> list[dict[str, Any]]:
        height, width = image.shape[:2]
        # 전처리 API가 갱신한 최신 GPKG를 요청마다 다시 읽습니다.
        parcels = CandidateParcelRepository(self._settings.candidate_gpkg_path)
        parcel = parcels.select_one(
            extent.min_x, extent.min_y, extent.max_x, extent.max_y, candidate_id
        )
        if parcel is None:
            return []

        candidate_px = self._map_to_pixel(parcel.geometry_3857, width, height, extent)
        if candidate_px.is_empty or candidate_px.area < self._settings.min_pixel_area:
            return []

        with self._predict_lock:
            result = self._model.predict(image, conf=self._settings.min_confidence, verbose=False)[0]
        masks = self._environment_masks(result, width, height, extent)
        class_masks = [item for item in masks if item["type"] in INSTALLABLE]
        best = self._best_type(candidate_px, class_masks)
        detected_type, _ = best if best else ("land", 0.0)

        roads = [item for item in masks if item["type"] == "road"]
        buildings = [item for item in masks if item["type"] == "building"]
        building_obstacles = buildings
        if parcel.candidate_type == "building":
            building_obstacles = [
                item for item in buildings
                if not parcel.geometry_3857.buffer(SPATIAL_TOLERANCE_M).intersects(item["map"])
            ]

        shape = self._shape(parcel.geometry_5179)
        real_area = parcel.candidate_area_m2
        usable_area = real_area * float(shape["shape_efficiency"])
        # 배치 시뮬레이션은 유지하되, 좌표 배열과 시각화 이미지는 반환하지 않습니다.
        panel_layout = self._layout(
            candidate_px,
            width,
            height,
            extent,
            roads,
            building_obstacles,
            detected_type,
        )
        valid_panels = [panel for panel in panel_layout if panel["valid"]]
        estimated_panel_count = len(valid_panels)

        candidate = {
            "pixel_area": round(float(candidate_px.area), 2),
            "real_area": round(real_area, 2),
            "distance_to_road_px": self._min_distance(candidate_px, roads, "px"),
            "distance_to_building_px": self._min_distance(candidate_px, buildings, "px"),
            "distance_to_road_m": self._min_distance(parcel.geometry_3857, roads, "map"),
            "distance_to_building_m": self._min_distance(
                parcel.geometry_3857,
                buildings,
                "map",
                SPATIAL_TOLERANCE_M if parcel.candidate_type == "building" else 0.0,
            ),
            "shape_score": shape["shape_score"], "shape_grade": shape["shape_grade"],
            "shape_efficiency": shape["shape_efficiency"], "recommended_layout": shape["recommended_layout"],
            "usable_area": round(usable_area, 2), "estimated_panel_count": estimated_panel_count,
            "candidate_id": parcel.candidate_id,
            "pnu": parcel.pnu,
        }
        return [candidate]

    def _environment_masks(self, result: Any, width: int, height: int, extent: Extent3857) -> list[dict[str, Any]]:
        if result.masks is None or result.boxes is None:
            return []
        output = []
        for poly, cls, conf in zip(result.masks.xy, result.boxes.cls.cpu().numpy(), result.boxes.conf.cpu().numpy()):
            geometry = self._geometry(poly)
            if geometry is not None:
                output.append({"type": result.names[int(cls)], "confidence": float(conf), "px": geometry, "map": self._pixel_to_map(geometry, width, height, extent)})
        return output

    @staticmethod
    def _best_type(candidate: BaseGeometry, masks: list[dict[str, Any]]) -> tuple[str, float] | None:
        if not masks or candidate.area <= 0:
            return None
        best = max(masks, key=lambda item: candidate.intersection(item["px"]).area)
        return best["type"], best["confidence"]

    def _layout(self, candidate: BaseGeometry, width: int, height: int, extent: Extent3857, roads: list[dict[str, Any]], buildings: list[dict[str, Any]], layout: str) -> list[dict[str, Any]]:
        mx = (extent.max_x - extent.min_x) / width; my = (extent.max_y - extent.min_y) / height
        pw, ph = max(1, round(PANEL_WIDTH_M / mx)), max(1, round(PANEL_HEIGHT_M / my))
        if layout == "Portrait": pw, ph = ph, pw
        gap, margin = max(1, round(PANEL_GAP_M / mx)), max(1, round(EDGE_MARGIN_M / mx))
        minx, miny, maxx, maxy = map(int, candidate.bounds)
        panels = []
        for y in range(miny, maxy - ph + 1, ph + gap):
            for x in range(minx, maxx - pw + 1, pw + gap):
                panel_px = box(x, y, x + pw, y + ph)
                if not candidate.buffer(-margin).contains(panel_px):
                    continue
                panel_map = self._pixel_to_map(panel_px, width, height, extent)
                rd = self._min_distance(panel_map, roads, "map", None)
                bd = self._min_distance(panel_map, buildings, "map", None)
                road_limit, building_limit = ROAD_CLEARANCE_PX * mx, BUILDING_CLEARANCE_PX * mx
                valid = (rd is None or rd >= road_limit) and (bd is None or bd >= building_limit)
                panels.append({"id": len(panels) + 1, "center": {"x": round(x + pw / 2, 3), "y": round(y + ph / 2, 3)}, "width": pw, "height": ph, "area": round(PANEL_WIDTH_M * PANEL_HEIGHT_M, 3), "valid": valid, "road_distance": rd, "building_distance": bd, "removed_reason": None if valid else ("Road" if rd is not None and rd < road_limit else "Building")})
        return panels

    @staticmethod
    def _shape(geometry: BaseGeometry) -> dict[str, Any]:
        area, perimeter = geometry.area, geometry.length
        efficiency = max(0.0, min(1.0, (4 * np.pi * area) / (perimeter ** 2 + 1e-6)))
        score = round(efficiency * 100, 2)
        grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
        minx, miny, maxx, maxy = geometry.bounds
        return {"shape_score": score, "shape_grade": grade, "shape_efficiency": round(efficiency, 3), "recommended_layout": "Landscape" if maxx-minx >= maxy-miny else "Portrait"}

    @staticmethod
    def _min_distance(geometry: BaseGeometry, masks: list[dict[str, Any]], key: str, tolerance: float = 0.0) -> float | None:
        if not masks: return None
        if tolerance and any(geometry.buffer(tolerance).intersects(mask[key]) for mask in masks):
            return 0.0
        values = [geometry.distance(mask[key]) for mask in masks]
        return round(float(min(values)), 2)

    @staticmethod
    def _geometry(points: np.ndarray) -> BaseGeometry | None:
        if len(points) < 3: return None
        geometry = Polygon(points)
        return geometry if geometry.is_valid else geometry.buffer(0)

    @staticmethod
    def _pixel_to_map(geometry: BaseGeometry, width: int, height: int, extent: Extent3857) -> BaseGeometry:
        return transform(lambda x, y, z=None: (extent.min_x + x * (extent.max_x - extent.min_x) / width, extent.max_y - y * (extent.max_y - extent.min_y) / height), geometry)

    @staticmethod
    def _map_to_pixel(geometry: BaseGeometry, width: int, height: int, extent: Extent3857) -> BaseGeometry:
        return transform(lambda x, y, z=None: ((x - extent.min_x) * width / (extent.max_x - extent.min_x), (extent.max_y - y) * height / (extent.max_y - extent.min_y)), geometry)

    @staticmethod
    def _outer_boundary(geometry: BaseGeometry) -> list[list[float]]:
        polygon = max(geometry.geoms, key=lambda item: item.area) if geometry.geom_type == "MultiPolygon" else geometry
        return [[round(x, 3), round(y, 3)] for x, y in polygon.exterior.coords]

    @staticmethod
    def _encode(
        image: np.ndarray,
        candidate: BaseGeometry | None = None,
        panels: list[dict[str, Any]] | None = None,
        size: tuple[int, int] | None = None,
        extension: str = ".png",
    ) -> str:
        output = image.copy()
        if candidate is not None:
            cv2.polylines(output, [np.asarray(candidate.exterior.coords if candidate.geom_type == "Polygon" else max(candidate.geoms, key=lambda p:p.area).exterior.coords, dtype=np.int32)], True, (0, 255, 0), 2)
        if size is not None:
            output = cv2.resize(output, size, interpolation=cv2.INTER_AREA)
        params = [cv2.IMWRITE_JPEG_QUALITY, FINAL_IMAGE_JPEG_QUALITY] if extension == ".jpg" else []
        success, buffer = cv2.imencode(extension, output, params)
        return base64.b64encode(buffer).decode("ascii") if success else ""

    @staticmethod
    def _debug_enabled() -> bool:
        return os.getenv("DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _new_debug_id(cls) -> str | None:
        if not cls._debug_enabled():
            return None
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    @staticmethod
    def _save_debug(debug_id: str | None, stage: str, image: np.ndarray) -> None:
        if debug_id is not None:
            cv2.imwrite(str(DEBUG_DIR / f"{debug_id}_{stage}.png"), image)

    @staticmethod
    def _draw_candidate(image: np.ndarray, candidate: BaseGeometry) -> np.ndarray:
        output = image.copy()
        polygon = candidate if candidate.geom_type == "Polygon" else max(candidate.geoms, key=lambda item: item.area)
        cv2.polylines(output, [np.asarray(polygon.exterior.coords, dtype=np.int32)], True, (0, 255, 0), 3)
        return output

    @staticmethod
    def _draw_environment(image: np.ndarray, masks: list[dict[str, Any]]) -> np.ndarray:
        output = image.copy()
        colors = {"road": (255, 255, 0), "building": (255, 0, 0), "parking_lot": (0, 0, 255)}
        for mask in masks:
            geometry = mask["px"]
            polygon = geometry if geometry.geom_type == "Polygon" else max(geometry.geoms, key=lambda item: item.area)
            cv2.polylines(output, [np.asarray(polygon.exterior.coords, dtype=np.int32)], True, colors.get(mask["type"], (255, 255, 255)), 2)
        return output

    @classmethod
    def _draw_panels(cls, image: np.ndarray, candidate: BaseGeometry, panels: list[dict[str, Any]]) -> np.ndarray:
        output = cls._draw_candidate(image, candidate)
        for panel in panels:
            center = panel["center"]
            x = int(round(center["x"] - panel["width"] / 2))
            y = int(round(center["y"] - panel["height"] / 2))
            color = (255, 180, 0) if panel["valid"] else (120, 120, 120)
            cv2.rectangle(output, (x, y), (x + panel["width"], y + panel["height"]), color, -1)
        return output

    @classmethod
    def _draw_final(
        cls,
        image: np.ndarray,
        candidate_geometry: BaseGeometry,
        masks: list[dict[str, Any]],
        candidate: dict[str, Any],
    ) -> np.ndarray:
        """Render already-calculated data only; this method changes no analysis result."""
        output = image.copy()
        colors = {
            "candidate": (0, 0, 255),  # Red (BGR)
            "valid_panel": (70, 160, 60),  # Green
        }

        # Show valid panels only. YOLO masks and removed panels remain available
        # to the analysis/JSON, but are intentionally omitted from this image.
        for panel in candidate["panel_layout"]:
            if not panel["valid"]:
                continue
            center = panel["center"]
            x = int(round(center["x"] - panel["width"] / 2))
            y = int(round(center["y"] - panel["height"] / 2))
            cv2.rectangle(output, (x, y), (x + panel["width"], y + panel["height"]), colors["valid_panel"], -1)
            cv2.rectangle(output, (x, y), (x + panel["width"], y + panel["height"]), (255, 255, 255), 1)

        # GPKG candidate boundary.
        polygon = candidate_geometry if candidate_geometry.geom_type == "Polygon" else max(candidate_geometry.geoms, key=lambda item: item.area)
        exterior = np.asarray(polygon.exterior.coords, dtype=np.int32)
        cv2.polylines(output, [exterior], True, colors["candidate"], 3)

        # Top-left analysis summary. Keep this limited to the five user-facing
        # metrics needed in the final image.
        info_lines = [
            "Analysis Summary",
            f"Area (m2): {candidate['real_area']:.2f}",
            f"Usable Area (m2): {candidate['usable_area']:.2f}",
            f"Panel Count: {candidate['estimated_panel_count']}",
            f"Road Distance (m): {cls._distance_text(candidate['distance_to_road_m'])}",
            f"Building Distance (m): {cls._distance_text(candidate['distance_to_building_m'])}",
        ]
        box_width, line_height = 300, 25
        box_height = 16 + len(info_lines) * line_height
        cv2.rectangle(output, (10, 10), (10 + box_width, 10 + box_height), (20, 20, 20), -1)
        for index, text in enumerate(info_lines):
            font_scale = 0.62 if index == 0 else 0.54
            thickness = 2 if index == 0 else 1
            cv2.putText(output, text, (20, 34 + index * line_height), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
        return output

    @staticmethod
    def _distance_text(value: float | None) -> str:
        return "N/A" if value is None else f"{value:.2f} m"
