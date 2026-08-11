from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from .config import get_settings
from .inference import Extent3857, YoloSegmentationService


logger = logging.getLogger(__name__)


class Prediction(BaseModel):
    pixel_area: float
    real_area: float = Field(description="Area in square metres, calculated from extent3857.")
    distance_to_road_px: float | None = Field(
        description="Shortest distance from a candidate polygon to a detected road, in pixels."
    )
    distance_to_building_px: float | None = Field(
        description="Shortest distance from a candidate polygon to a detected building, in pixels."
    )
    distance_to_road_m: float | None = Field(
        description="Shortest distance from a candidate polygon to a detected road, in metres."
    )
    distance_to_building_m: float | None = Field(
        description="Shortest distance from a candidate polygon to a detected building, in metres."
    )
    shape_score: float = Field(description="Panel-placement suitability score based on polygon shape.")
    shape_grade: str = Field(description="Shape suitability grade from A (best) to E.")
    shape_efficiency: float = Field(
        description="Usable-area efficiency derived from the shape grade."
    )
    recommended_layout: str = Field(description="Recommended panel orientation: Landscape or Portrait.")
    usable_area: float = Field(
        description="Area in square metres after shape and packing efficiencies are applied."
    )
    estimated_panel_count: int = Field(
        description="Estimated count of 550W panels that fit in the usable area."
    )
    candidate_id: str | None = None
    pnu: str | None = None


class PredictResponse(BaseModel):
    predictions: list[Prediction]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The model is loaded once per FastAPI process, never per request.
    app.state.predictor = YoloSegmentationService(get_settings())
    yield


app = FastAPI(title="Solar AI Inference API", version="1.0.0", lifespan=lifespan)


def parse_extent3857(value: str) -> Extent3857:
    """Accept JSON object, JSON array, or comma-separated minX,minY,maxX,maxY."""
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in value.split(",")]

    try:
        if isinstance(parsed, dict):
            extent = Extent3857(
                min_x=float(parsed["minX"]), min_y=float(parsed["minY"]),
                max_x=float(parsed["maxX"]), max_y=float(parsed["maxY"]),
            )
        elif isinstance(parsed, list) and len(parsed) == 4:
            extent = Extent3857(*(float(item) for item in parsed))
        else:
            raise ValueError
        extent.validate()
        return extent
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "extent3857 must be {minX,minY,maxX,maxY}, "
                "[minX,minY,maxX,maxY], or 'minX,minY,maxX,maxY'."
            ),
        ) from exc


@app.post("/predict", response_model=PredictResponse)
async def predict(
    request: Request,
    image: Annotated[UploadFile, File(description="PNG/JPEG map image")],
    extent3857: Annotated[str, Form(description="Map extent in EPSG:3857")],
    candidate_id: Annotated[str | None, Form(description="GPKG candidate_id")]=None,
) -> dict[str, Any]:

    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(
            status_code=415,
            detail="image must be an image file.",
        )

    raw_image = await image.read()

    try:
        decoded = cv2.imdecode(
            np.frombuffer(raw_image, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
    finally:
        await image.close()

    if decoded is None:
        raise HTTPException(
            status_code=422,
            detail="image could not be decoded by OpenCV.",
        )

    extent = parse_extent3857(extent3857)

    try:
        predictions = request.app.state.predictor.predict(
            decoded,
            extent,
            candidate_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Vision prediction failed (candidate_id=%s)",
            candidate_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return {"predictions": predictions}