# 핸드폰 "앱" 으로 설치하기

이 프로젝트는 PWA(설치형 웹앱)라서, 세 단계로 점점 더 네이티브 앱에 가깝게 만들 수 있다.
아이콘(`web/static/icons/`)과 `manifest.webmanifest` 는 이미 앱 패키징 규격에 맞춰 준비돼 있다.

---

## 0. 공통 전제 — HTTPS 주소

안드로이드 "앱 설치" 프롬프트, iOS 정식 PWA, APK 빌드 **모두 HTTPS 가 필요**하다.
(사내망 `http://IP:8070` 로는 홈화면 바로가기까지만 됨)

가장 쉬운 방법 (사내 IT 승인 없이):

1. 이 PC 와 핸드폰에 **Tailscale** 설치 → 같은 계정 로그인
2. 관리콘솔 <https://login.tailscale.com/admin/dns> 에서 **MagicDNS** + **HTTPS Certificates** 활성화
3. 서버 실행 상태에서 `server\https_tailscale.bat` 더블클릭
   → `https://<PC이름>.<tailnet>.ts.net` 주소가 출력됨. 이후 이 주소를 사용.

---

## A. 설치형 PWA — 공수 0, 코드 그대로

- **안드로이드 Chrome**: 위 https 주소 접속 → 메뉴(⋮) → **"앱 설치"**
  → 앱 서랍에 등록, 별도 창, 스플래시, 주소창 없음 (거의 네이티브)
- **아이폰 Safari**: 공유 → **"홈 화면에 추가"** → 전체화면 아이콘

대부분 이걸로 충분하다. APK 가 꼭 필요할 때만 아래로.

---

## B. APK 만들기 — PWABuilder (웹, 로컬 도구 불필요) ★추천

1. <https://www.pwabuilder.com> 접속
2. 위 https 주소 입력 → **Start**
3. **Android** → **Generate Package**
   - Package ID 예: `com.sinheung.compressor`
   - 서명 키: "새로 생성" 선택(비밀번호·키파일 안전하게 보관) 또는 기존 키 업로드
4. 받은 zip 안의 `.apk` 를 핸드폰에 복사 →
   설정에서 "이 출처 허용"(알 수 없는 앱 설치) 후 설치
   - Play 스토어에 올리려면 zip 안 `.aab` 사용
   - 사내 배포는 MDM(Intune 등)에 `.apk`/`.aab` 업로드

> 주소창을 완전히 없애려면(TWA Digital Asset Links):
> zip 안 `assetlinks.json` 을 서버가 `/.well-known/assetlinks.json` 으로 서빙해야 함.
> Tailscale serve 환경에선 생략해도 상단에 얇은 도메인 표시가 있는 형태로 동작한다.

---

## C. APK 만들기 — Bubblewrap (로컬 CLI)

전제: Node.js, JDK 17, Android SDK

```bash
npm i -g @bubblewrap/cli
bubblewrap init --manifest https://<주소>/manifest.webmanifest
# 이 폴더의 twa-manifest.json 값을 참고해 입력
bubblewrap build
# → app-release-signed.apk 생성
```

---

## iOS 정식 앱 (스토어/사내배포)

애플은 사이드로딩이 막혀 있어 **회사 Apple Developer 계정**(Enterprise $299/년 또는
Ad-Hoc UDID 등록, 100대)이 필요하다. 그 단계에서는 **Capacitor** 로 감싸서
기존 REST API 를 그대로 쓰고 FCM/APNs 푸시·Face ID 까지 붙이는 것을 권장.

---

## 아이콘 다시 만들기

```bash
.venv\Scripts\python.exe -m pip install -r scripts\requirements-build.txt
.venv\Scripts\python.exe scripts\gen_icons.py
```
