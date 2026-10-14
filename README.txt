렉시오(Lexio) 온라인 멀티플레이
================================

■ 구성
  server.py          서버(계정+실시간 게임 엔진)
  static/index.html  웹 클라이언트(로그인→로비→게임)
  data/users.json    가입 계정 저장(비밀번호는 해시로만 저장)
  실행.bat           서버 실행 바로가기

■ 서버 켜기
  1) 실행.bat 더블클릭  (또는 명령창에서  python server.py )
  2) 창에 접속 주소가 표시됩니다. 창을 닫으면 서버가 꺼집니다.

■ 접속 방법
  - 내 PC:        http://localhost:8000
  - 같은 와이파이: http://<서버PC_IP>:8000   (예: http://192.168.0.5:8000)
                   친구 휴대폰/노트북을 같은 와이파이에 연결 후 이 주소 입력
  - 아이디/비밀번호로 회원가입 → 로그인 → 방 만들기/입장 → 게임 시작

■ 관리자 (회원·게임내역 조회)
  - 일반 로그인 화면에서 아이디 admin / 비번 1234 로 로그인하면 관리자 대시보드가 열립니다.
    (별도 주소 없음. 환경변수 ADMIN_ID / ADMIN_PW 로 바꿀 수 있음)
  - 회원 가입 후부터 각자의 게임수·승리·게임내역이 계속 누적됩니다(DB 연결 시 영구 보관).

■ 음성 기능
  - 게임 중 "🎤 길게 눌러 말하기" 버튼을 누르는 동안 녹음 → 떼면 같은 방 사람들에게 음성 전송(푸시투토크).
    첫 사용 시 브라우저가 마이크 권한을 물어봅니다. 인터넷(https)에서만 동작합니다.
  - 라운드 시작 시 "선(先)" 플레이어를 음성으로 안내합니다(구름3 보유자).

■ 게임 방법
  - 방장이 방을 만들고(인원 2~5), 친구들이 로비에서 입장
  - 빈 자리는 'AI 추가'로 채울 수 있음(사람만으로도 가능)
  - 모든 자리가 차면 방장이 '게임 시작'
  - 타일을 눌러 선택 → '내기' / 낼 수 없으면 '패스'
  - 라운드 종료 시 자동 정산(숫자2 보유 ×2 페널티), 방장이 '다음 라운드'

■ 집 밖 친구도 부르기 (인터넷 공개)  ※ 서버 PC가 켜져 있어야 함

  방법 A) 터널 — 가끔 모일 때 (무료, 계정 불필요)  ★추천
     1) 실행.bat 으로 게임 서버를 켠다
     2) 인터넷공개.bat 더블클릭
     3) 창에 나오는  https://xxxxx.trycloudflare.com  주소를 친구에게 전달
        → 집 밖 친구가 그 주소로 바로 접속!
     * 이 창을 닫으면 외부 접속이 끊깁니다. 켤 때마다 주소가 새로 바뀝니다.
     * cloudflared 설치는 1회만:  winget install --id Cloudflare.cloudflared
       (이미 설치돼 있으면 인터넷공개.bat 만 실행하면 됩니다.)

  방법 B) 24시간 상시 서비스 (클라우드 배포 - 고정 주소 + 계정 영구보관)
     PC를 꺼도 항상 켜져 있는 고정 주소(https://<이름>.onrender.com)로 운영.
     계정을 영구 보관하려고 무료 Postgres(Neon)를 연결합니다.
     배포 파일은 이미 준비됨: requirements.txt / Dockerfile / render.yaml / Procfile

     [1단계] 코드를 GitHub에 올리기
       - GitHub CLI 로 한 번만 인증:  gh auth login
         (GitHub.com → HTTPS → "Login with a web browser" → 코드 복사 → 브라우저 승인)
       - 저장소 생성+업로드:
         gh repo create lexio-online --public --source . --remote origin --push

     [2단계] 무료 DB 만들기 (Neon - 계정 영구보관용, 신용카드 불필요)
       - https://neon.tech 가입 → New Project 생성
       - "Connection string"(postgresql://... 로 시작) 복사해 둔다

     [3단계] Render 에 배포
       - https://render.com 가입(구글 로그인 가능)
       - New → Blueprint → 방금 만든 GitHub 저장소 선택 (render.yaml 자동 인식)
       - DATABASE_URL 입력칸에 [2단계]의 Neon 연결 문자열 붙여넣기
       - Apply → 몇 분 뒤 https://<이름>.onrender.com 완성!

     주의: 무료 플랜은 15분 미사용 시 잠들어 첫 접속이 ~30초 느릴 수 있습니다
           (고정 주소·계정은 그대로 유지됩니다).

■ 방화벽
  같은 와이파이의 친구가 접속이 안 되면, 처음 실행 시 뜨는
  'Windows 보안 경고'에서 (개인/공용) 네트워크 허용을 체크하세요.

■ 포트 변경
  기본 8000. 바꾸려면 명령창에서:  set PORT=9000 && python server.py
