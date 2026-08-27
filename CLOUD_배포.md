# 클라우드 배포 (Render 무료)

배포하면: 깨끗한 `https://...onrender.com` 주소 → 안드로이드 "앱 설치" / 아이폰 정상 동작,
회사 방화벽·ngrok 경고페이지 없음, **PC 를 켜둘 필요 없음**.

시뮬레이터가 클라우드 안에서 돌아 데이터가 계속 생성된다.
(실제 PLC 연결은 나중에 현장 게이트웨이가 클라우드로 값을 올려보내는 방식으로 확장)

---

## 1. GitHub 저장소 만들기

이 폴더는 이미 git 초기화 + 첫 커밋이 되어 있다.
GitHub 에 빈 저장소를 만들고(예: `compressor-remote`, Private 가능), 주소를 복사한 뒤:

```powershell
cd "C:\Users\SH-\Desktop\CODE 사용\콤퓨레샤 핸드폰 제어"
git remote add origin https://github.com/<내계정>/compressor-remote.git
git branch -M main
git push -u origin main
```

> git 이 없으면: `winget install --id Git.Git` 후 새 터미널.
> 최초 push 시 GitHub 로그인 창이 뜬다.

---

## 2. Render 에서 배포

1. <https://render.com> 가입 (GitHub 계정으로 로그인 가능, 카드 불필요)
2. 대시보드 → **New +** → **Blueprint**
3. 방금 push 한 저장소 선택 → Render 가 `render.yaml` 을 읽어 자동 구성
4. **Apply** → 3~5분 빌드 후 `https://compressor-remote-xxxx.onrender.com` 주소 발급
5. 그 주소로 접속 → 로그인 `admin / admin1234`

(Blueprint 대신 수동: New + → **Web Service** → 저장소 선택 →
 Build `pip install -r requirements.txt` /
 Start `gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT wsgi:app` /
 Health Check Path `/login` / Plan Free)

---

## 3. 핸드폰에 앱 설치

- 폰 브라우저에서 `https://<받은주소>/join`
- **안드로이드 Chrome**: 메뉴 ⋮ → **앱 설치** → 앱 서랍에 아이콘(WebAPK)
- **아이폰 Safari**: 공유 → **홈 화면에 추가**
- 경고 페이지 없음. 오프라인 캐시·알림도 동작.

APK 파일(.apk/.aab, MDM·스토어용)이 필요하면:
`scripts\set_apk_host.py <받은주소의 호스트>` → www.pwabuilder.com 에 그 https 주소 입력.

---

## 알아둘 점 (무료 플랜)

- **15분간 접속이 없으면 잠자기** → 다음 접속 시 30~60초 콜드스타트. 대시보드를 열어두면 3초마다 폴링해서 안 잠. 상시 가동이 필요하면 유료($7/월~).
- **디스크가 임시** → 재배포/잠자기 후 깨어나면 **계정·이력 초기화**(기본계정 admin/admin1234 로 리셋). 영구 저장은 `render.yaml` 의 disk 주석 해제 + 유료 디스크.
- 세션 시크릿은 `COMPRESSOR_SECRET_KEY` 를 Render 가 자동 생성해 유지한다.

## 운영 전

- 배포 후 로그인 → 좌측 "내 계정" 에서 비밀번호 변경 (단, 무료 플랜은 재시작 시 초기화됨 → 유료 디스크 필요)
- `config.yaml` 의 기본 계정을 실제 사용자로 교체하려면 코드 수정 후 재배포
