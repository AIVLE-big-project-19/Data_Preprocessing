# 태양광 최종 Merged CSV 생성 API

이 프로젝트의 목표는 **주소만 입력하면 `Merged_Test_Data.csv`의 ML용 34개 컬럼을 모두 생성**하는 것입니다.

## 최종 처리 흐름

```text
주소 입력
→ VWorld 지오코딩: longitude / latitude
→ 주소 정규화: 시도 / 시군구 / region_group
→ Global Solar Atlas: GHI / PVOUT / DNI / DIF / GTI / TEMP
→ Global Wind Atlas: 10m / 50m / 100m 풍속
→ DEM: elevation / slope / aspect / hillshade / southness
→ VWorld 지적도: PNU 확보
→ 건축물대장: 토지/건물 판정
→ OSM 전력망: 거리 / 5km 개수·길이 / 최대전압
→ source_id_ml / 설치구분 / label / asset_type_code 생성
→ 정확히 34개 컬럼으로 Merged_Test_Data.csv 저장
```

## 최종 34개 컬럼

```text
source_id_ml
address_ml
longitude
latitude
시도
시군구
자산구분_ML
설치구분
label
ghi_avg_daily
pvout_avg_daily
dni_avg_daily
dif_avg_daily
gti_avg_daily
temp_avg
wind_speed_10m
wind_speed_50m
wind_speed_100m
slope_avg
slope_dir
elevation_avg
Hillshade
Southness
distance_to_substation_km
distance_to_powerline_km
substation_count_5km
powerline_length_5km_km
high_voltage_line_nearby_5km
substation_max_voltage_kv
powerline_max_voltage_kv
substation_max_voltage_kv_missing
powerline_max_voltage_kv_missing
asset_type_code
region_group
```

`substation_max_voltage_kv`와 `powerline_max_voltage_kv`는 OSM 원본에 voltage 태그가 없으면 NaN일 수 있습니다. 이 경우 대응하는 `*_missing=1`이므로 ML 입력상 정상적인 결측 표현입니다. 그 외 필수 컬럼이 비면 기본 설정에서는 최종 CSV 저장을 중단합니다.

## 필요한 원천 데이터

```text
data/input/Merged_Test_Data.csv          # 목표 스키마 + 기존 ID 기준
data/source_data/solar/                  # Global Solar Atlas YearlyMonthlyTotals ZIP
data/source_data/wind/                   # 10m/50m/100m GeoTIFF
data/source_data/dem/                    # DEM (+ 필요 시 slope/aspect/hillshade 자동 생성)
data/gis/korea_power_grid.gpkg           # substations / powerlines
```

외부 API 키:

```env
VWORLD_API_KEY=...
VWORLD_DOMAIN=...
BUILDING_API_KEY=...
```

## 실행

```bat
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn api.main:app --host 127.0.0.1 --port 8001 --reload
```

Swagger: `http://127.0.0.1:8001/docs`

## 주소 한 건 → 완성 Merged 행

`POST /preprocess`

```json
{
  "address": "충청남도 태안군 안면읍 승언리 646-18",
  "save": true
}
```

응답의 `results`에는 상태 컬럼이 아니라 **ML용 34개 컬럼 전체**가 반환됩니다.

## 여러 주소 → 최종 데이터셋

```json
{
  "addresses": [
    "충청남도 태안군 안면읍 승언리 646-18",
    "대전광역시 서구 장안동 513-10"
  ],
  "save": true
}
```

## 현재 Merged_Test_Data의 빈 값 전체 재생성

업로드 파일에서 `address_ml`도 주소 컬럼으로 인식합니다. 따라서 현재 데이터셋 자체를 업로드하면 모든 주소를 다시 처리할 수 있습니다.

```bat
curl -X POST "http://127.0.0.1:8001/preprocess/file" ^
  -F "file=@data/input/Merged_Test_Data.csv" ^
  -F "save=true"
```

620개 주소라면 620개를 순서대로 최신 원천 데이터에서 다시 계산해 기존 ID에 upsert합니다.

## 출력 파일

```text
data/result/Merged_Test_Data.csv       # 누적 최종 데이터셋, 정확히 34개 컬럼
data/result/Latest_Merged_Rows.csv     # 이번 요청에서 처리한 행만 34개 컬럼
data/result/Preprocessing_Audit.csv    # 처리 성공/실패 및 누락 컬럼 확인용
data/result/Preprocessed_Parcels.*     # 지적 Polygon 보조 결과
```

`Merged_Test_Data.csv`에는 디버그/status 컬럼을 넣지 않습니다. `Preprocessing_Audit.csv`로 분리합니다.

## 완성 여부 확인

`GET /schema`에서 목표 34개 컬럼을 확인할 수 있습니다.

기본값:

```env
REQUIRE_COMPLETE_MERGED_ROWS=true
```

이 설정에서는 새로 처리한 행에서 필수 ML 값이 하나라도 생성되지 않으면 불완전한 행을 최종 데이터셋에 조용히 저장하지 않고 오류를 반환합니다.
