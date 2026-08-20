# Solar Aivle 데이터 전처리·Vision 연동

주소를 입력하면 태양광 후보지 분석에 필요한 환경·지형·전력망·공간정보를 수집하고, Ranking ML에서 사용하는 34개 컬럼의 `Merged_Test_Data.csv`를 생성하는 프로젝트입니다.

전처리 서버가 후보지 주소와 공간정보를 구성한 뒤 Vision AI 서버에 항공영상과 필지 정보를 전달하며, Vision 결과는 별도 CSV로 저장됩니다.

## 1. 시스템 구성

이 저장소는 다음 두 영역으로 구성됩니다.

| 영역 | 기본 포트 | 역할 |
|---|---:|---|
| `preprocessing/` | 8001 | 주소 기반 지오코딩, 래스터·지형·지적·건물·전력망 Feature 생성 및 결과 저장 |
| `vision/` | 8000 | YOLO Segmentation, 후보지 형태 분석, 이격거리 및 패널 배치 수 산출 |

```text
사용자 또는 파일
      │ 주소 입력
      ▼
Preprocessing API :8001
      ├─ VWorld 지오코딩·지적도·건물·항공영상
      ├─ Solar/Wind/DEM 래스터
      ├─ OSM 전력망
      └─ 34개 ML 컬럼 및 필지 GPKG 생성
                    │
                    │ 항공영상 + extent3857 + candidate_id
                    ▼
              Vision API :8000
                    ├─ YOLO Segmentation
                    ├─ 도로·건물 이격거리
                    ├─ 형태 점수·가용면적
                    └─ 유효 패널 수
                    │
                    ▼
       Vision Feature 및 결합 CSV 저장
```

Vision 연동을 사용하지 않을 경우 `VISION_ENABLED=false`로 설정하면 34개 ML 전처리만 실행할 수 있습니다.

## 2. 저장소 구조

```text
Data_Preprocessing/
├─ README.md
├─ preprocessing/
│  ├─ FINAL_DATASET_GUIDE.md       # 34개 ML 컬럼 중심의 상세 안내
│  ├─ requirements.txt             # 전처리 서버 Python 패키지
│  ├─ run_server.bat               # Windows 실행 스크립트
│  ├─ api/
│  │  ├─ __init__.py
│  │  ├─ main.py                   # FastAPI 요청·응답 및 엔드포인트
│  │  ├─ config.py                 # 환경변수, 파일 경로, 처리 기준값
│  │  ├─ pipeline.py               # 전체 전처리·저장·Vision 연동 파이프라인
│  │  └─ terrain.py                # DEM 기반 지형 Feature 계산
│  ├─ data/
│  │  ├─ input/                    # 기준 Merged CSV
│  │  ├─ source_data/              # Solar·Wind·DEM·OSM 원천 파일 배치 위치
│  │  ├─ gis/                      # 가공된 전력망 GPKG
│  │  ├─ work/                     # 압축 해제·중간 작업 파일
│  │  └─ result/                   # 최종 CSV·감사 파일·필지 GPKG
│  └─ test_data/                   # 파일 업로드 테스트 데이터
└─ vision/
   └─ api/
      ├─ config.py                 # YOLO 모델·후보지 GPKG 경로
      ├─ main.py                   # `/predict` API
      ├─ inference.py              # Segmentation·형태·패널 배치 계산
      └─ gpkg_candidates.py        # GPKG에서 요청 후보지 선택
```

`data/source_data/`와 일부 `data/result/` 파일은 실행 중 생성하거나 사용자가 직접 배치하는 경로이므로 저장소에 없을 수 있습니다.

## 3. 파일별 역할

### Preprocessing API

| 파일 | 주요 역할 |
|---|---|
| `preprocessing/api/main.py` | JSON·파일 입력 검증, `/preprocess` 요청 처리, 상태·스키마 조회 |
| `preprocessing/api/config.py` | VWorld, OSM, Vision, 래스터, 결과 파일 경로와 실행 옵션 관리 |
| `preprocessing/api/pipeline.py` | 주소 정규화부터 결과 저장까지 전체 파이프라인 실행 |
| `preprocessing/api/terrain.py` | DEM 주변 픽셀에서 고도·경사도·경사 방향·Hillshade·Southness 계산 |

### Vision API

| 파일 | 주요 역할 |
|---|---|
| `vision/api/main.py` | 이미지, 지도 범위, 후보 ID를 입력받아 `/predict` 실행 |
| `vision/api/config.py` | YOLO 모델 파일과 전처리 GPKG 경로 로드 |
| `vision/api/gpkg_candidates.py` | `candidate_id`와 지도 범위로 분석할 필지 하나를 선택 |
| `vision/api/inference.py` | YOLO 마스크, 형태 분석, 이격거리, 패널 배치 가능 수 계산 |

## 4. 전처리 흐름

`preprocessing/api/pipeline.py`의 `run_pipeline()`이 전체 처리를 조정합니다.

### 4.1 입력 정리

1. JSON 또는 CSV·XLSX에서 주소를 읽습니다.
2. 공백·중복 주소를 제거합니다.
3. 최대 처리 건수 설정을 확인합니다.

### 4.2 주소별 Feature 생성

각 주소는 `process_one_address()`에서 다음 순서로 처리됩니다.

| 순서 | 처리 | 주요 결과 |
|---:|---|---|
| 1 | VWorld 지오코딩 | 경도, 위도, 확인 주소 |
| 2 | 행정구역 정규화 | 시도, 시군구, region_group |
| 3 | Solar·Wind 래스터 조회 | GHI, PVOUT, DNI, DIF, GTI, 기온, 풍속 |
| 4 | DEM 지형 분석 | 고도, 경사도, 경사 방향, Hillshade, Southness |
| 5 | VWorld 지적도 조회 | PNU, 지목, 면적, 필지 Polygon |
| 6 | VWorld 건물 조회 | 토지형·건물형 판정 보조정보 |
| 7 | OSM 전력망 분석 | 변전소·전력선 거리, 5km 통계, 최대전압 |
| 8 | 처리 상태 판정 | SUCCESS, PARTIAL_SUCCESS, FAILED |

### 4.3 기존 데이터와 병합

- 기준 파일의 기존 `source_id_ml`을 보존합니다.
- 동일한 주소는 기존 행을 갱신하고 신규 주소는 새 ID를 부여합니다.
- 조회에 실패한 단계는 정상적으로 새 값이 생성됐을 때만 기존 값을 교체합니다.
- 최종 ML 데이터에는 상태·디버그 컬럼을 넣지 않고 34개 컬럼만 저장합니다.
- 처리 상태와 누락 컬럼은 `Preprocessing_Audit.csv`에 분리합니다.

### 4.4 Vision 연동

`save=true`이고 `VISION_ENABLED=true`이면 다음 작업을 추가로 수행합니다.

1. 전처리 서버가 필지 Polygon을 `Preprocessed_Parcels.gpkg`에 저장합니다.
2. VWorld에서 후보지 중심의 1024×1024 항공영상을 요청합니다.
3. 이미지 범위에 대응하는 EPSG:3857 `extent3857`을 계산합니다.
4. 이미지, `extent3857`, `candidate_id`를 Vision `/predict`에 전달합니다.
5. Vision 서버가 동일한 GPKG에서 `candidate_id`에 맞는 필지를 선택합니다.
6. YOLO 결과와 필지 경계를 이용해 이격거리·형태·패널 수를 계산합니다.
7. 결과를 `Vision_Features.csv`와 결합 CSV에 저장합니다.

## 5. 생성 Feature

### ML용 34개 컬럼

| 분류 | 컬럼 |
|---|---|
| 식별·지역 | `source_id_ml`, `address_ml`, `longitude`, `latitude`, `시도`, `시군구`, `region_group` |
| 후보지 정보 | `자산구분_ML`, `설치구분`, `label`, `asset_type_code` |
| 일사·발전 | `ghi_avg_daily`, `pvout_avg_daily`, `dni_avg_daily`, `dif_avg_daily`, `gti_avg_daily`, `temp_avg` |
| 풍속 | `wind_speed_10m`, `wind_speed_50m`, `wind_speed_100m` |
| 지형 | `slope_avg`, `slope_dir`, `elevation_avg`, `Hillshade`, `Southness` |
| 전력망 | `distance_to_substation_km`, `distance_to_powerline_km`, `substation_count_5km`, `powerline_length_5km_km`, `high_voltage_line_nearby_5km`, 최대전압 및 결측 플래그 |

`substation_max_voltage_kv` 또는 `powerline_max_voltage_kv`는 OSM 원본에 전압 태그가 없으면 비어 있을 수 있습니다. 이 경우 대응하는 `*_missing` 컬럼이 `1`이면 정상적인 관측 불가 값으로 처리합니다.

정확한 컬럼 순서는 `GET /schema` 또는 `preprocessing/FINAL_DATASET_GUIDE.md`에서 확인할 수 있습니다.

### Vision 결과 14개 컬럼

```text
pixel_area
real_area
distance_to_road_px
distance_to_building_px
distance_to_road_m
distance_to_building_m
shape_score
shape_grade
shape_efficiency
recommended_layout
usable_area
estimated_panel_count
candidate_id
pnu
```

`estimated_panel_count`는 단순 면적 나눗셈이 아니라 필지 경계, 패널 간격, 가장자리 여백, 도로·건물 이격조건을 통과한 패널의 개수입니다.

## 6. 입력 데이터와 외부 자원

### 필수 기준 파일

```text
preprocessing/data/input/Merged_Test_Data.csv
```

최초 실행 시 목표 스키마와 기존 ID를 유지하기 위한 기준 파일로 사용합니다.

### 래스터·공간 원천 파일

```text
preprocessing/data/source_data/solar/   # Global Solar Atlas ZIP 또는 GeoTIFF
preprocessing/data/source_data/wind/    # 10m·50m·100m 풍속 GeoTIFF
preprocessing/data/source_data/dem/     # DEM 및 선택적 지형 파생 TIFF
preprocessing/data/source_data/osm/     # South Korea OSM PBF
preprocessing/data/gis/                 # 전력망 GPKG
```

OSM PBF와 전력망 GPKG는 설정에 따라 자동 다운로드·생성할 수 있습니다. Solar, Wind, DEM 파일은 사용자가 지정 경로에 배치해야 합니다.

### 외부 서비스

| 서비스 | 사용 목적 |
|---|---|
| VWorld Search API | 주소 지오코딩 |
| VWorld 연속지적도 WFS | PNU·지목·필지 Polygon |
| VWorld 건물 WFS | 건물 존재 여부 및 자산 유형 판정 |
| VWorld Image API | Vision 분석용 항공영상 |
| Geofabrik/OpenStreetMap | 변전소·전력선 공간정보 |

## 7. 환경변수

현재 저장소에는 `.env.example`이 포함되어 있지 않으므로 다음 내용을 참고해 파일을 직접 생성합니다.

### `preprocessing/.env`

```env
VWORLD_API_KEY=발급받은_VWorld_API_KEY
VWORLD_DOMAIN=VWorld에_등록한_도메인

VISION_ENABLED=true
VISION_API_URL=http://127.0.0.1:8000/predict
VISION_TIMEOUT_SECONDS=180
VISION_IMAGE_ZOOM=18
VISION_IMAGE_SIZE=1024

INPUT_FILE=false
REQUIRE_COMPLETE_MERGED_ROWS=false
```

`VWORLD_DOMAIN`은 VWorld API 키에 등록된 허용 도메인과 일치해야 합니다.

### Vision 프로젝트 `.env`

```env
MODEL_PATH=bestv2.pt
MODEL_VERSION=bestv2
MIN_CONFIDENCE=0.50
MIN_PIXEL_AREA=700
CANDIDATE_GPKG_PATH=C:\절대경로\preprocessing\data\result\Preprocessed_Parcels.gpkg
```

`CANDIDATE_GPKG_PATH`는 전처리 서버가 생성하는 최신 `Preprocessed_Parcels.gpkg`를 가리켜야 합니다.

## 8. 실행 방법

### 8.1 전처리 서버 설치

```powershell
cd preprocessing
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 8.2 Vision 코드 적용

이 저장소의 `vision/api/`는 기존 [visionAI 저장소](https://github.com/AIVLE-big-project-19/visionAI)의 연동 코드를 교체하기 위한 파일입니다.

```text
vision/api/config.py
vision/api/main.py
vision/api/inference.py
vision/api/gpkg_candidates.py
```

Vision AI 프로젝트에 위 파일을 적용하고 해당 프로젝트의 환경에서 YOLO 모델과 의존성을 준비합니다.

### 8.3 서버 실행 순서

Vision 서버를 먼저 실행합니다.

```powershell
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

그다음 전처리 서버를 실행합니다.

```powershell
cd preprocessing
.venv\Scripts\activate
python -m uvicorn api.main:app --host 0.0.0.0 --port 8001
```

- 전처리 Swagger: `http://127.0.0.1:8001/docs`
- Vision Swagger: `http://127.0.0.1:8000/docs`

## 9. API

### 전처리 API

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/health` | 서버 상태와 초기화 경고 확인 |
| GET | `/sources` | API 키·래스터·전력망·Vision 설정 상태 확인 |
| GET | `/schema` | 최종 34개 ML 컬럼과 조건부 결측 컬럼 확인 |
| POST | `/preprocess` | 주소 한 건 또는 여러 건 전처리 |
| POST | `/preprocess/file` | 주소 컬럼이 있는 CSV·XLSX 업로드 전처리 |

### 주소 한 건

```json
{
  "address": "충청남도 태안군 안면읍 승언리 646-18",
  "save": true
}
```

### 여러 주소

```json
{
  "addresses": [
    "충청남도 태안군 안면읍 승언리 646-18",
    "대전광역시 서구 장안동 513-10"
  ],
  "save": true
}
```

`address`와 `addresses`는 동시에 사용할 수 없습니다.

### 파일 업로드

```powershell
curl -X POST "http://127.0.0.1:8001/preprocess/file" `
  -F "file=@data/input/Merged_Test_Data.csv" `
  -F "save=true"
```

파일에서는 `address`, `address_ml`, `주소`, `소재지`, `도로명주소`, `지번주소` 등의 주소 컬럼을 인식합니다.

## 10. 출력 파일

| 파일 | 내용 |
|---|---|
| `data/result/Merged_Test_Data.csv` | 기존 데이터와 신규 결과를 upsert한 누적 34개 ML 컬럼 |
| `data/result/Latest_Merged_Rows.csv` | 이번 요청에서 추가·갱신된 34개 ML 행 |
| `data/result/Preprocessing_Audit.csv` | 단계별 처리 상태와 누락 컬럼 |
| `data/result/Preprocessed_Parcels.geojson` | Vision 연동용 최신 후보지 Polygon |
| `data/result/Preprocessed_Parcels.gpkg` | Vision 서버가 `candidate_id`로 조회하는 필지 GPKG |
| `data/result/Vision_Features.csv` | 후보별 Vision 결과 누적 |
| `data/result/Latest_Merged_Rows_With_Vision.csv` | 이번 ML 전처리 행과 Vision Feature 결합 결과 |

`save=false`이면 파일을 저장하지 않고 API 응답으로만 결과를 확인합니다.

## 11. 처리 상태와 오류 확인

- `SUCCESS`: 주요 단계가 모두 정상 처리됨
- `PARTIAL_SUCCESS`: 일부 외부 조회 또는 원천 파일이 누락됨
- `FAILED`: 지오코딩 등 필수 단계 실패

최종 34개 ML 컬럼에는 상태 컬럼을 포함하지 않습니다. 다음 위치에서 처리 상태를 확인합니다.

- API 응답의 `summary`
- API 응답의 `result_files`
- `Preprocessing_Audit.csv`
- `GET /sources`

`REQUIRE_COMPLETE_MERGED_ROWS=true`이면 조건부 결측을 제외한 필수 값이 하나라도 없을 때 최종 저장을 중단합니다. 기본 코드 설정은 `false`입니다.

## 12. 연계 저장소

- [Vision AI](https://github.com/AIVLE-big-project-19/visionAI): 항공영상 Segmentation 및 패널 배치 분석
- [Ranking ML](https://github.com/AIVLE-big-project-19/Ranking_ML): 전처리·Vision 결과 기반 후보지 점수·등급·순위 산출

## 13. 주의사항

- VWorld 키·도메인이 일치하지 않으면 지오코딩, 지적도, 건물, 항공영상 조회가 실패할 수 있습니다.
- 래스터 파일의 좌표계와 범위가 후보지 좌표를 포함해야 합니다.
- `CANDIDATE_GPKG_PATH`는 전처리 서버가 실제 생성한 GPKG의 절대경로로 설정하는 것이 안전합니다.
- Vision 결과는 사전검토용 추정값이며 실제 현장 측량이나 구조안전 검토를 대체하지 않습니다.
