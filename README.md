# off/on server

MMS → Firebase → feed

## setup

1. Firebase 프로젝트 만들고 `firebase-key.json` 받기
2. 환경변수 설정:
   - `TWILIO_ACCOUNT_SID`
   - `TWILIO_AUTH_TOKEN`
   - `OFFON_NUMBER` (+15707077010)
3. Twilio 웹훅 URL: `https://your-app.railway.app/mms`

## endpoints

- `GET /` — 서버 상태 확인
- `POST /mms` — Twilio 웹훅 (MMS 수신)
- `GET /api/posts` — 피드 가져오기
- `GET /api/user/:handle` — 프로필
- `POST /api/register` — 회원가입
