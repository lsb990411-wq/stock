# Vercel 배포 가이드

법인 주식 매매일지(Streamlit)는 **장시간 실행되는 Python 프로세스**이므로, Vercel의 일반 Serverless Function(`@vercel/python`)으로는 동작하지 않습니다.  
이 저장소는 **Vercel Container Service**(`Dockerfile.vercel`)로 배포하도록 구성되어 있습니다.

## 1. 사전 준비

- GitHub 저장소: `https://github.com/lsb990411-wq/stock`
- Supabase 프로젝트 URL · **anon public key**
- Vercel 계정 (Container Service 지원 플랜 — 팀/Pro 등, 대시보드에서 **Framework: Services** 확인)

## 2. Vercel에 프로젝트 연결

1. [Vercel Dashboard](https://vercel.com/dashboard) → **Add New… → Project**
2. GitHub `stock` 저장소 Import
3. **Framework Preset** 이 자동 감지되지 않으면 **Other / Services** 로 설정
4. Root Directory: `.` (저장소 루트)
5. `vercel.json` · `Dockerfile.vercel` 이 루트에 있으면 별도 Build Command 불필요

CLI로 배포할 경우:

```bash
npm i -g vercel
vercel login
vercel link
vercel deploy          # Preview
vercel deploy --prod   # Production
```

## 3. 환경 변수 (필수)

**Vercel Dashboard → Project → Settings → Environment Variables**

| 이름 | 값 | 비고 |
|------|-----|------|
| `SUPABASE_URL` | `https://xxxx.supabase.co` | Project Settings → API → Project URL |
| `SUPABASE_KEY` | `eyJ...` 또는 `sb_publishable_...` | **anon public** 키 (service_role 금지) |

- **Production**, **Preview**, **Development** 모두 체크 후 저장
- 재배포(Deployments → Redeploy)해야 컨테이너에 반영됩니다
- `.streamlit/secrets.toml` 은 git에 없으며, Vercel에서는 **환경 변수만** 사용합니다

`src/supabase_client.py` 는 아래 순서로 읽습니다.

1. `SUPABASE_URL` / `SUPABASE_KEY` (또는 `SUPABASE_ANON_KEY`)
2. 로컬 `.streamlit/secrets.toml`
3. Streamlit secrets

로컬 개발용 템플릿: `.env.example` 참고 (Vercel에는 `.env` 파일을 올리지 마세요).

## 4. 설정 파일 요약

| 파일 | 역할 |
|------|------|
| `vercel.json` | Container Service `web` + 전 경로 rewrite |
| `Dockerfile.vercel` | Python 3.13 + Streamlit 실행 (`$PORT`) |
| `requirements.txt` | Python 의존성 |
| `.python-version` | Python 3.13 |
| `.vercelignore` | secrets·백업·PDF 등 빌드 제외 |
| `.streamlit/config.toml` | headless / fileWatcher 비활성 |

## 5. 배포 후 확인

- 배포 URL 접속 → 사이드바 **DB: supabase** 표시
- 사업자·거래 목록이 Supabase와 일치하는지 확인
- 업로드·전표 다운로드 등 무거운 작업은 첫 요청 시 수 초 걸릴 수 있음

## 6. 제한·주의

- **로컬 JSON 백업**(`data/backups/`)은 Vercel 컨테이너 디스크에 영구 저장되지 않습니다. 앱은 클라우드에서 자동 백업을 건너뜁니다.
- DB는 Supabase가 원본입니다. 백업은 Supabase Dashboard 또는 로컬 PC에서 실행하세요.
- Streamlit WebSocket 특성상, 일부 프록시/캐시 설정에서 새로고침 이슈가 있을 수 있습니다.
- OCR(PDF 스캔)은 Docker 이미지에 `tesseract-ocr` 포함. 실패 시 텍스트 PDF·Excel 업로드를 사용하세요.

## 7. Streamlit Cloud (대안)

Vercel Container가 어렵다면 [Streamlit Community Cloud](https://share.streamlit.io/) 가 더 단순합니다.

- 같은 GitHub repo 연결
- **Secrets** (TOML):

```toml
[supabase]
url = "https://xxxx.supabase.co"
key = "your_anon_key"
```

- Main file: `app.py`

## 8. 문제 해결

| 증상 | 확인 |
|------|------|
| DB 연결 실패 | 환경 변수 이름·값, Redeploy |
| 빌드 실패 (용량) | `.vercelignore` 에 `data/backups`, PDF 제외 확인 |
| 빈 화면 / 502 | Deployment Logs, Container Service 활성 여부 |
| 데이터 안 보임 | Supabase RLS·anon key 권한 |

로그: Vercel Dashboard → Deployments → 해당 배포 → **Runtime Logs**
