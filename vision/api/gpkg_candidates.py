from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pyproj import Transformer
from shapely import wkb
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


PROJECT_DIR = Path(__file__).resolve().parent.parent
CANDIDATE_GPKG_PATH = PROJECT_DIR / "data" / "candidate_parcels.gpkg"


@dataclass(frozen=True)
class CandidateParcel:
    candidate_id: str
    candidate_type: str
    geometry_5179: BaseGeometry
    geometry_3857: BaseGeometry
    candidate_area_m2: float
    pnu: str | None
    address: str

    @property
    def parcel_area_m2(self) -> float:
        """Compatibility name used by the existing inference calculation."""
        return self.candidate_area_m2


class CandidateParcelRepository:
    """Loads the GPKG candidate source once and selects one parcel per extent."""

    def __init__(self, gpkg_path: Path = CANDIDATE_GPKG_PATH) -> None:
        if not gpkg_path.is_file():
            raise RuntimeError(f"Candidate GPKG file does not exist: {gpkg_path}")

        with sqlite3.connect(gpkg_path) as connection:
            table_name, source_srs_id = connection.execute(
                "SELECT table_name, srs_id FROM gpkg_contents "
                "WHERE data_type = 'features' LIMIT 1"
            ).fetchone()
            rows = connection.execute(
                f'SELECT geom, candidate_id, candidate_type, candidate_area_m2, pnu, address '
                f'FROM "{table_name}"'
            ).fetchall()

        transformer_5179 = Transformer.from_crs(source_srs_id, 5179, always_xy=True)
        transformer_3857 = Transformer.from_crs(source_srs_id, 3857, always_xy=True)
        self._parcels: list[CandidateParcel] = []
        for geometry_blob, candidate_id, candidate_type, candidate_area, pnu, address in rows:
            source_geometry = _read_gpkg_geometry(geometry_blob)
            if source_geometry.is_empty:
                continue
            if not source_geometry.is_valid:
                source_geometry = source_geometry.buffer(0)
            if source_geometry.is_empty:
                continue

            geometry_5179 = transform(transformer_5179.transform, source_geometry)
            geometry_3857 = transform(transformer_3857.transform, source_geometry)
            self._parcels.append(
                CandidateParcel(
                    candidate_id=str(candidate_id or pnu or ""),
                    candidate_type=str(candidate_type or "land"),
                    geometry_5179=geometry_5179,
                    geometry_3857=geometry_3857,
                    candidate_area_m2=float(candidate_area or geometry_5179.area),
                    pnu=str(pnu) if pnu is not None else None,
                    address=str(address or ""),
                )
            )

    def select_one(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        candidate_id: str | None = None,
    ) -> CandidateParcel | None:
        """Choose the parcel nearest the requested map centre among intersecting parcels."""
        requested_extent = box(min_x, min_y, max_x, max_y)
        matching = [
            parcel for parcel in self._parcels
            if parcel.geometry_3857.intersects(requested_extent)
            and (candidate_id is None or parcel.candidate_id == candidate_id)
        ]
        if not matching:
            return None
        centre = requested_extent.centroid
        return min(matching, key=lambda parcel: parcel.geometry_3857.distance(centre))


def _read_gpkg_geometry(geometry_blob: bytes) -> BaseGeometry:
    """Extract standard WKB from a GeoPackage geometry binary."""
    if geometry_blob[:2] != b"GP":
        raise RuntimeError("Invalid GeoPackage geometry header.")
    envelope_indicator = (geometry_blob[3] >> 1) & 0b111
    envelope_size = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope_indicator)
    if envelope_size is None:
        raise RuntimeError("Unsupported GeoPackage geometry envelope.")
    return wkb.loads(geometry_blob[8 + envelope_size :])
