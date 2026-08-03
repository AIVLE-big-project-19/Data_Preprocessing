# 태양광 후보지 전처리 API

주소를 기준으로 태양광 후보지의 좌표, 일사량·풍속·지형, 지적도, 건축물대장, 전력망 Feature를 생성하고 최신 결과를 저장하는 FastAPI 서버입니다.

## 처리 흐름

```text
주소 입력
→ VWorld 지오코딩(road → parcel, 최대 5회)
→ 래스터 Feature 추출
→ 연속지적도 Polygon/PNU 조회
→ 건축물대장 조회 및 건물·토지 판정
→ 기존 전력망 GPKG 기반 Feature 추출
→ 기존 결과와 주소 비교
→ 기존 ID 유지 + 최신 정상값 업데이트 / 신규 ID 발급
→ CSV, GeoJSON, GPKG 저장
```

## 주요 파일

```text
api/__init__.py
api/config.py
api/pipeline.py
api/main.py
requirements.txt
.env
run_server.bat
test_data/preprocess_single.json
test_data/preprocess_batch.json
test_data/candidate_addresses.csv
```

## 필수 준비

### 1. API 키

프로젝트 루트의 `.env`에 입력합니다.

```env
VWORLD_API_KEY=실제키
VWORLD_DOMAIN=VWorld에 등록한 도메인
BUILDING_API_KEY=공공데이터포털 일반 인증키
```

### 2. 입력 방식 선택

`POST /preprocess`가 사용할 입력 방식을 `.env`에서 선택합니다.

#### POST JSON 사용

```env
INPUT_FILE=false
```

이 경우 Swagger 또는 외부 서비스가 POST한 `address` 또는 `addresses`를 처리합니다.

#### 지정 파일 사용

```env
INPUT_FILE=true
INPUT_DATA_PATH=test_data/candidate_addresses.csv
```

이 경우 `POST /preprocess`의 주소값은 사용하지 않고 `INPUT_DATA_PATH`에 지정된 CSV/XLSX/XLS 파일을 읽습니다.

- 허용 주소 컬럼: `address`, `주소`, `소재지`, `도로명주소`, `지번주소`
- `.env` 변경 후에는 서버를 재시작해야 합니다.
- `POST /preprocess/file`은 `INPUT_FILE` 설정과 관계없이 업로드한 파일을 직접 처리합니다.

### 3. 기준 CSV

최초 실행 전에 다음 파일을 넣습니다.

```text
data/input/Merged_Test_Data.csv
```

기존 ML 컬럼 순서와 `자산구분_ML`-`asset_type_code` 매핑을 이 파일에서 읽습니다.

### 4. 원천 래스터

```text
data/source_data/solar/  Global Solar Atlas YearlyMonthlyTotals ZIP
data/source_data/wind/   10m, 50m, 100m 풍속 GeoTIFF
data/source_data/dem/    elevation, slope, slope_dir/aspect, hillshade, southness GeoTIFF
```

### 5. 전력망 GPKG

전력망은 자동 생성하지 않고 기존 파일을 사용합니다.

```text
data/gis/korea_power_grid.gpkg
├─ substations
└─ powerlines
```

`.env`는 다음처럼 설정합니다.

```env
POWER_GRID_GPKG_PATH=data/gis/korea_power_grid.gpkg
AUTO_DOWNLOAD_OSM=false
AUTO_BUILD_POWER_GRID=false
FORCE_REBUILD_POWER_GRID=false
```

GPKG가 없으면 전력망 Feature는 `MISSING_FILE` 상태로 처리됩니다.

## 설치 및 실행

Windows 명령 프롬프트 기준:

```bat
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --host 127.0.0.1 --port 8001 --reload
```

Swagger:

```text
http://127.0.0.1:8001/docs
```

## 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| GET | `/health` | 서버 상태 확인 |
| GET | `/sources` | API 키·래스터·전력망·기준 CSV 상태 확인 |
| POST | `/preprocess` | `.env` 설정에 따라 POST JSON 또는 지정 파일 처리 |
| POST | `/preprocess/file` | 업로드한 주소 CSV/XLSX/XLS 처리 |

## 테스트

### POST JSON 모드

`.env`:

```env
INPUT_FILE=false
```

단건:

```bat
curl -X POST "http://127.0.0.1:8001/preprocess" ^
  -H "Content-Type: application/json" ^
  --data-binary "@test_data/preprocess_single.json"
```

다건:

```bat
curl -X POST "http://127.0.0.1:8001/preprocess" ^
  -H "Content-Type: application/json" ^
  --data-binary "@test_data/preprocess_batch.json"
```

요청 예시:

```json
{
  "addresses": [
    "전라남도 보성군 회천면 화죽리 1228-19",
    "인천광역시 계양구 작전동 862-40"
  ],
  "save": true
}
```

### 지정 파일 모드

`.env`:

```env
INPUT_FILE=true
INPUT_DATA_PATH=test_data/candidate_addresses.csv
```

요청 본문에는 주소를 넣지 않아도 됩니다.

```json
{
  "save": true
}
```

### 파일 업로드

```bat
curl -X POST "http://127.0.0.1:8001/preprocess/file" ^
  -F "file=@test_data/candidate_addresses.csv" ^
  -F "save=true"
```

## 저장 결과

```text
data/result/Preprocessed_Candidates.csv
data/result/Preprocessed_Parcels.geojson
data/result/Preprocessed_Parcels.gpkg
```

저장 규칙:

- 주소 중복은 모든 최신 Feature를 계산한 뒤 저장 직전에 확인합니다.
- 같은 주소가 있으면 기존 `source_id_ml`을 유지합니다.
- 새 조회가 성공한 단계는 최신값으로 교체합니다.
- 새 조회가 실패한 단계는 기존 정상값을 보존하고 최신 오류 상태만 기록합니다.
- 신규 주소는 사용되지 않은 가장 작은 `SOLAR_숫자` ID를 발급합니다.
- 정상적인 `NOT_FOUND` 결과는 실제 최신 결과로 반영합니다.
- Polygon 성공 결과는 기존 공간데이터를 교체합니다.

## 주요 상태

```text
processing_status: SUCCESS / PARTIAL_SUCCESS / FAILED
change_status: NEW / UPDATED / UNCHANGED / UPDATE_PARTIAL / FAILED
```

## 주의사항

- `data/input`, `data/source_data`, `data/gis` 폴더와 필수 입력 파일은 사용자가 준비합니다.
- `data/work`, `data/result`는 실행 중 필요한 경우 생성됩니다.
- `INPUT_FILE=true`일 때 `INPUT_DATA_PATH` 파일이 없으면 요청이 실패합니다.
- 전력망 GPKG가 없으면 나머지 전처리는 계속되지만 `grid_status=MISSING_FILE`이 기록됩니다.
- `Preprocessed_Candidates.csv`를 Excel에서 열어 둔 상태로 저장하면 Windows 파일 잠금으로 `PermissionError`가 발생할 수 있습니다. 저장 요청 전에 Excel을 닫아야 합니다.
- GeoJSON과 GPKG는 유효한 지적 Polygon을 얻은 후보가 있을 때만 생성 또는 갱신됩니다.
