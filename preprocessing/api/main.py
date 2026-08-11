from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import pipeline
from .config import settings


class PreprocessRequest(BaseModel):
    """주소 한 건 또는 여러 건을 받는 테스트 요청입니다."""

    model_config = ConfigDict(extra="forbid")

    address: str | None = Field(default=None, min_length=1, max_length=500)
    addresses: list[str] | None = Field(default=None, min_length=1)
    save: bool = True

    @model_validator(mode="after")
    def validate_addresses(self) -> "PreprocessRequest":
        if self.address is not None and self.addresses:
            raise ValueError("address와 addresses를 동시에 사용할 수 없습니다.")
        if not settings.input_file and self.address is None and not self.addresses:
            raise ValueError("INPUT_FILE=false일 때는 address 또는 addresses가 필요합니다.")
        return self

    def to_list(self) -> list[str]:
        return [self.address] if self.address is not None else list(self.addresses or [])


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pipeline.initialize_resources()
    yield
    await pipeline.close_resources()


app = FastAPI(
    title="태양광 후보지 전처리 API",
    description=(
        "주소만 입력받아 Merged_Test_Data의 ML용 34개 컬럼을 생성하고 "
        "완성된 Merged_Test_Data.csv와 최신 처리 행을 저장합니다."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "solar-preprocessing-api",
        "version": "1.1.0",
        "warnings": pipeline.resources.initialization_warnings,
    }


@app.get("/sources")
def sources() -> dict:
    """API 키와 원천 데이터 로드 상태를 확인합니다."""
    return pipeline.get_source_status()


@app.get("/schema")
def schema() -> dict:
    """최종 Merged_Test_Data.csv의 정확한 ML 스키마를 반환합니다."""
    return {
        "column_count": len(pipeline.MERGED_OUTPUT_COLUMNS),
        "columns": pipeline.MERGED_OUTPUT_COLUMNS,
        "conditional_missing": pipeline.CONDITIONAL_MISSING_COLUMNS,
    }


@app.post("/preprocess")
async def preprocess_json(request: PreprocessRequest) -> dict:
    """주소 한 건/여러 건을 완성형 34컬럼 Merged 행으로 생성합니다."""
    try:
        addresses = pipeline.resolve_input_addresses(request.to_list())
        return await pipeline.run_pipeline(addresses, save=request.save)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except pipeline.ResourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"전처리 중 오류가 발생했습니다: {type(exc).__name__}: {exc}") from exc


@app.post("/preprocess/file")
async def preprocess_file(
    file: Annotated[
        UploadFile,
        File(description="address/address_ml/주소 컬럼이 있는 CSV/XLSX/XLS 파일"),
    ],
    save: Annotated[bool, Form()] = True,
) -> dict:
    """주소 목록 파일을 업로드해 전처리합니다."""
    try:
        content = await file.read()
        addresses = pipeline.read_address_file(file.filename or "", content)
        return await pipeline.run_pipeline(addresses, save=save)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except pipeline.ResourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"전처리 중 오류가 발생했습니다: {type(exc).__name__}: {exc}") from exc
