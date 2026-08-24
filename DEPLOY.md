# Vercel 배포 가이드

이 앱은 **Streamlit**(상시 실행 Python 서버)입니다.  
Vercel의 일반 Serverless Function(`@vercel/python`, Flask/FastAPI식 요청 단위 실행)으로는 WebSocket·세션이 동작하지 않습니다.

배포 방식은 **Vercel Container Service** (`Dockerfile.vercel`)입니다.

## 환경 변수 (필수)

**Vercel Dashboard → Project → Settings → Environment Variables**

| 변수명 | 예시 | 설명 |
|--------|------|------|
| `SUPABASE_URL` | `https://xxxx.supabase.co` | Supabase → Project Settings → API → Project URL |
| `SUPABASE_KEY` | `eyJ...` 또는 `sb_publishable_...` | **anon / publishable** 키. `service_role` 은 넣지 마세요. |

설정 순서:

1. **Key**에 `SUPABASE_URL` 입력 → **Value**에 프로젝트 URL 붙여넣기
2. Environment에서 **Production / Preview / Development** 모두 선택 → Save
3. 같은 방식으로 `SUPABASE_KEY` 추가
4. **Deployments → 최신 배포 → Redeploy** (환경 변수는 재배포 후 반영)

앱은 `src/supabase_client.py`에서 다음 순서로 읽습니다.

1. `SUPABASE_URL` + `SUPABASE_KEY` (또는 `SUPABASE_ANON_KEY`)
2. 로컬 `.streamlit/secrets.toml`의 `[supabase] url` / `key`
3. Streamlit Cloud secrets

로컬 템플릿: `.env.example` (git에는 실제 키를 올리지 마세요).

## 왜 서버리스 JSON만으로는 안 되는가

아래 같은 설정은 Flask/FastAPI용이며 Streamlit과 맞지 않습니다.

```json
{ "builds": [{ "src": "app.py", "use": "@vercel/python" }] }
```

`streamlit run`은 장시간 프로세스이므로 `vercel.json`의 **services + container + `root`** 가 필요합니다.  
이전 배포 실패(`missing required property root`)는 `root` 누락 때문이었습니다.

## 배포 방법

대시보드:

1. [vercel.com](https://vercel.com/new) → GitHub `lsb990411-wq/stock` Import
2. Framework: **Other** / **Services**
3. Root Directory: `.`
4. 위 환경 변수 등록 후 Deploy

CLI (이미 `vercel link` 된 경우):

```bash
npx vercel@latest deploy --prod --yes
```

## 파일 역할

| 파일 | 역할 |
|------|------|
| `vercel.json` | Container `web` (`root: "."`) + 전 경로 rewrite |
| `Dockerfile.vercel` | Python 3.13 + Streamlit (`$PORT`) |
| `requirements.txt` | Python 패키지 |
| `.python-version` | 3.13 |
| `.vercelignore` | secrets, 백업, PDF 제외 |
| `.streamlit/config.toml` | headless / file watcher off |

## 의존성 참고

실제 코드에서 쓰는 패키지만 `requirements.txt`에 넣었습니다.

- `openpyxl` / `xlrd`: 엑셀 전표·업로드
- `pdfplumber`: PDF 파싱
- **weasyprint는 사용하지 않습니다.** (PDF 생성은 엑셀 전표로 처리)

## 제한

- 컨테이너 디스크는 휘발성 → 클라우드에서 로컬 JSON 백업은 건너뜀
- 데이터 원본은 Supabase
- OCR은 이미지에 tesseract 포함

## 문제 해결

| 증상 | 확인 |
|------|------|
| `Invalid vercel.json - missing required property root` | `services.web.root` 가 `"."` 인지 |
| DB 연결 실패 | 환경 변수 + Redeploy |
| Schema / Container 플랜 오류 | Vercel 플랜이 Container/Services 를 지원하는지 |
| 502 | Runtime Logs |

대안: [Streamlit Community Cloud](https://share.streamlit.io/) + secrets.toml `[supabase]`.
