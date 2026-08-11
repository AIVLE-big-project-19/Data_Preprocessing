[적용 순서]

1. preprocessing/api의 pipeline.py, config.py를 전처리 프로젝트 api 폴더에 덮어씁니다.
2. vision/api의 네 파일을 Vision AI 프로젝트 api 폴더에 덮어씁니다.
3. 각 .env.example을 참고해 preprocessing/.env와 vision/.env를 만듭니다.
4. Vision 서버를 먼저 8000번 포트로 실행합니다.
5. 전처리 서버를 8001번 포트로 실행합니다.

[환경변수 설정]

1. Preprocessor: VWorld API 키 설정

preprocessing/.env에 VWorld API 키와 해당 키에 등록한 허용 도메인을 입력합니다.

VWORLD_API_KEY=본인_VWORLD_API_KEY
VWORLD_DOMAIN=https://github.com/gyungeun-kim/big_proj
VISION_ENABLED=true
VISION_API_URL=http://127.0.0.1:8000/predict
VISION_TIMEOUT_SECONDS=180
VISION_IMAGE_ZOOM=18
VISION_IMAGE_SIZE=1024

VWORLD_API_KEY가 없거나 VWORLD_DOMAIN이 VWorld에 등록한 도메인과 다르면
지오코딩, 폴리곤 또는 항공영상 요청이 실패할 수 있습니다.

2. Vision: 모델 및 전처리 GPKG 경로 설정

vision/.env에 YOLO 모델과 전처리 서버가 생성하는 GPKG의 절대 경로를 입력합니다.

MODEL_PATH=bestv2.pt
MODEL_VERSION=bestv2
MIN_CONFIDENCE=0.50
MIN_PIXEL_AREA=700
CANDIDATE_GPKG_PATH=C:\Users\User\Downloads\vision_preprocess_integration\preprocessing\data\result\Preprocessed_Parcels.gpkg

CANDIDATE_GPKG_PATH는 Vision 폴더 내부의 임의 GPKG가 아니라 반드시
Preprocessor가 생성한 다음 파일을 가리켜야 합니다.

<통합 프로젝트 경로>\preprocessing\data\result\Preprocessed_Parcels.gpkg

예시:
C:\Users\User\Downloads\vision_preprocess_integration\preprocessing\data\result\Preprocessed_Parcels.gpkg

Preprocessor에서 save=true로 요청하면 위 경로의 GPKG가 생성 또는 갱신됩니다.
Vision 서버는 /predict 요청마다 이 GPKG를 다시 읽어 candidate_id에 맞는 필지를 선택합니다.
경로를 변경했다면 Vision 서버를 완전히 종료한 후 다시 실행해야 합니다.

[실행]

Vision AI 프로젝트:
python -m pip install -r requirements.txt
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

전처리 프로젝트:
python -m pip install -r requirements.txt
python -m uvicorn api.main:app --host 0.0.0.0 --port 8001

[출력]

data/result/Merged_Test_Data.csv                       기존 ML 34컬럼
data/result/Vision_Features.csv                        Vision 결과 누적
data/result/Latest_Merged_Rows_With_Vision.csv         최신 34컬럼+Vision 결과
data/result/Preprocessed_Parcels.gpkg                  Vision이 읽는 최신 필지

Vision 서버는 요청마다 최신 GPKG를 다시 읽고 candidate_id로 정확한 필지를 선택합니다.

[전처리 전용 Vision 출력]

패널 배치 그리드 계산은 수행하지만 좌표 배열은 반환하지 않습니다.
estimated_panel_count에는 경계·간격·도로·건물 조건을 통과한 valid panel 개수를 저장합니다.
최종 시각화, 디버그 이미지, JPEG/Base64 이미지는 생성하지 않습니다.
Vision /predict는 predictions 안에 요청한 14개 필드만 반환합니다.
