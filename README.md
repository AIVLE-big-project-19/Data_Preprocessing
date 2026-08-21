# Solar Aivle 데이터 전처리·Vision 연동

주소를 입력하면 태양광 후보지 분석에 필요한 환경·지형·전력망·공간정보를 수집하고, Ranking ML에서 사용하는 34개 ML Feature와 Vision AI 분석 결과를 생성하는 프로젝트입니다.

전처리 서버가 후보지 주소와 공간정보를 구성한 뒤 Vision AI 서버에 항공영상과 필지 정보를 전달합니다. 최종 정제본은 ML Feature에 Vision 면적·이격거리·형태·패널 배치 결과와 실행 정보를 결합한 **319개 후보지, 54개 컬럼**으로 구성됩니다.

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
                    ├─ 유효 패널 수
                    └─ 패널별 배치 좌표
                    │
                    ▼
       ML + Vision 최종 입력 데이터 저장
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
6. YOLO 결과와 필지 경계를 이용해 이격거리·형태·설치 가능 면적을 계산합니다.
7. 필지 내부에 패널을 배치하고 도로·건물 이격조건을 통과한 유효 패널 수를 계산합니다.
8. 필지 형상과 패널별 배치 위치를 GeoJSON으로 변환합니다.
9. ML Feature, Vision 결과, 실행 상태 및 GeoJSON 결과를 최종 입력 데이터로 결합합니다.

## 5. 최종 입력 데이터

현재 최종 정제본 `전처리_완료_입력_데이터.csv`는 **319개 후보지, 54개 컬럼**으로 구성됩니다.

전체 컬럼은 **ML 입력 34개 + Vision 분석 결과 14개 + Vision 실행 상태 3개 + 재계산 여부 1개 + 필지·패널 배치 JSON 2개**로 구성됩니다.

### 컬럼 구성

| 구분 | 주요 컬럼 | 설명 |
|---|---|---|
| 후보지 식별 | `source_id_ml`, `candidate_id`, `pnu` | 후보지 ID, Vision AI 매칭 ID, 필지고유번호 |
| 위치 정보 | `address_ml`, `longitude`, `latitude`, `시도`, `시군구`, `region_group` | 주소, 경위도 및 지역 구분 |
| 후보 속성 | `자산구분_ML`, `설치구분`, `label`, `asset_type_code` | 토지·건물 구분, 설치 여부 및 ML 라벨 |
| 태양광 자원 | `ghi_avg_daily`, `pvout_avg_daily`, `dni_avg_daily`, `dif_avg_daily`, `gti_avg_daily` | 후보지 위치의 일평균 태양광 자원 피처 |
| 기상 정보 | `temp_avg`, `wind_speed_10m`, `wind_speed_50m`, `wind_speed_100m` | 평균기온과 높이별 풍속 |
| 지형 정보 | `slope_avg`, `slope_dir`, `elevation_avg`, `Hillshade`, `Southness` | 평균 경사도·방향, 고도, 음영 및 남향성 |
| 전력 인프라 | `distance_to_substation_km`, `distance_to_powerline_km` | 가장 가까운 변전소 및 전력선까지의 거리 |
| 전력망 분포 | `substation_count_5km`, `powerline_length_5km_km`, `high_voltage_line_nearby_5km` | 후보지 반경 5km 내 변전소 수, 전력선 길이 및 고압선 존재 여부 |
| 전압 정보 | `substation_max_voltage_kv`, `powerline_max_voltage_kv` | 주변 변전소 및 전력선의 최대 전압 |
| 결측 표시 | `substation_max_voltage_kv_missing`, `powerline_max_voltage_kv_missing` | 최대 전압 정보의 결측 여부 |
| Vision 면적 | `pixel_area`, `real_area`, `usable_area` | 영상 내 필지 픽셀 면적, 실제 필지 면적 및 형태 효율을 반영한 설치 가능 면적 |
| Vision 이격거리 | `distance_to_road_px`, `distance_to_building_px`, `distance_to_road_m`, `distance_to_building_m` | 도로 및 건물과의 픽셀·미터 단위 이격거리 |
| 형태 분석 | `shape_score`, `shape_grade`, `shape_efficiency`, `recommended_layout` | 후보지 형태 점수·등급·효율 및 권장 패널 배치 방향 |
| 설치 규모 | `estimated_panel_count` | 필지 경계와 배치 조건을 통과한 예상 설치 가능 패널 수 |
| Vision 실행 상태 | `vision_status`, `vision_error`, `vision_model_version` | Vision AI 처리 결과, 오류 내용 및 사용 모델 버전 |
| 재계산 여부 | `recomputed` | 최종 보정 과정에서 Vision 결과를 다시 계산했는지 여부 |
| 필지 형상 | `parcel_geometry_json` | 분석에 사용한 필지 경계를 GeoJSON Polygon으로 저장한 값 |
| 패널 배치 | `panel_layout_json` | 필지 내부에 배치한 개별 패널의 위치와 유효 여부를 GeoJSON FeatureCollection으로 저장한 값 |

`usable_area`는 `real_area × shape_efficiency`로 계산한 형태 효율 반영 면적입니다.

`estimated_panel_count`는 배치 시뮬레이션에서 유효 판정을 받은 패널의 개수입니다. 실제 패널별 배치 좌표와 유효 여부는 `panel_layout_json`에서 확인할 수 있습니다.

`parcel_geometry_json`과 `panel_layout_json`은 경위도 좌표계의 GeoJSON 형식입니다. `panel_layout_json`의 각 Feature에는 패널 ID와 `valid` 판정이 포함되므로 지도에서 필지 경계와 패널 배치 결과를 함께 시각화할 수 있습니다.

도로 또는 건물이 영상에서 검출되지 않은 후보지는 해당 이격거리 값이 비어 있을 수 있습니다. 이 경우 처리 성공 여부와 오류 내용은 `vision_status`, `vision_error`에서 확인합니다.

`substation_max_voltage_kv` 또는 `powerline_max_voltage_kv`는 OSM 원본에 전압 태그가 없으면 비어 있을 수 있습니다. 이 경우 대응하는 `*_missing` 컬럼이 `1`이면 정상적인 관측 불가 값으로 처리합니다.

ML용 34개 컬럼의 정확한 순서는 `GET /schema` 또는 `preprocessing/FINAL_DATASET_GUIDE.md`에서 확인할 수 있습니다.

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

## 8. 출력 파일

| 파일 | 내용 |
|---|---|
| `data/result/Merged_Test_Data.csv` | 기존 데이터와 신규 결과를 upsert한 누적 34개 ML 컬럼 |
| `data/result/Latest_Merged_Rows.csv` | 이번 요청에서 추가·갱신된 34개 ML 행 |
| `data/result/Preprocessing_Audit.csv` | 단계별 처리 상태와 누락 컬럼 |
| `data/result/Preprocessed_Parcels.geojson` | Vision 연동용 최신 후보지 Polygon |
| `data/result/Preprocessed_Parcels.gpkg` | Vision 서버가 `candidate_id`로 조회하는 필지 GPKG |
| `data/result/Vision_Features.csv` | 후보별 Vision 결과 누적 |
| `data/result/Latest_Merged_Rows_With_Vision.csv` | 이번 ML 전처리 행과 Vision Feature 결합 결과 |
| `전처리_완료_입력_데이터.csv` | ML Feature, Vision 결과, 필지 형상 및 패널 배치 정보를 결합한 최종 54개 컬럼 데이터 |

## 13. 주의사항

- VWorld 키·도메인이 일치하지 않으면 지오코딩, 지적도, 건물, 항공영상 조회가 실패할 수 있습니다.
- 래스터 파일의 좌표계와 범위가 후보지 좌표를 포함해야 합니다.
- `CANDIDATE_GPKG_PATH`는 전처리 서버가 실제 생성한 GPKG의 절대경로로 설정하는 것이 안전합니다.
- Vision 결과는 사전검토용 추정값이며 실제 현장 측량이나 구조안전 검토를 대체하지 않습니다.
