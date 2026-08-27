# Tailscale + 앱 설치 / APK (선택: Tailscale)

핸드폰이 사내망 밖(LTE 등)에서도 접속하고, "앱"으로 설치되게 하는 절차.
Tailscale = 내 계정 기기끼리만 연결되는 사설 VPN. 공개 인터넷 노출 없음.

---

## 1. Tailscale 설치 (1회)

1. 계정: <https://login.tailscale.com/start> → Google/MS 계정 로그인
2. **이 PC**: <https://tailscale.com/download/windows> 설치 → 트레이 아이콘에서 로그인
3. **핸드폰**: App Store / Play 스토어에서 `Tailscale` → **같은 계정** 로그인 → VPN 허용 → ON
4. 관리콘솔 <https://login.tailscale.com/admin/dns> 에서
   - **MagicDNS** 켜기
   - **HTTPS Certificates** 켜기

> 회사 관리(도메인) PC면 설치에 관리자 권한 / IT 승인이 필요할 수 있음.

---

## 2. HTTPS 주소 만들기

서버가 실행 중인 상태에서 (`실행.bat` 또는 `server\상시실행_설치.bat`):

```
server\https_tailscale.bat   더블클릭
```

출력되는 주소를 복사:  `https://<PC이름>.<tailnet이름>.ts.net`
해제는 `server\https_tailscale_stop.bat`.

---

## 3. 앱으로 설치

### 방법 A — 그냥 설치 (APK 파일 불필요) ★대부분 이걸로 끝

핸드폰에서 `https://<...>.ts.net/join` 접속 (QR 스캔 또는 주소 입력):

- **안드로이드 Chrome**: 메뉴 ⋮ → **"앱 설치"**
  → Chrome 이 뒤에서 실제 앱(WebAPK)을 만들어 설치함. 앱 서랍에 아이콘, 별도 창.
- **아이폰 Safari**: 공유 □↑ → **"홈 화면에 추가"** → 전체화면 앱 아이콘.

HTTPS 라서 오프라인 캐시·알림도 정상 동작.

### 방법 B — .apk 파일이 필요할 때 (MDM 배포 / Play 스토어 / Play서비스 없는 기기)

**PWABuilder (웹, 로컬 설치 불필요) — 추천**
1. `scripts\set_apk_host.py <PC이름>.<tailnet>.ts.net` 실행 → `twa-manifest.json` 자동 채움
2. <https://www.pwabuilder.com> 접속 → 위 `https://...ts.net` 입력 → Start
3. Android → Generate Package → 서명 키 "새로 생성"(비밀번호 보관)
4. 받은 zip 의 `.apk` 를 폰에 설치 / `.aab` 를 스토어·MDM 에 업로드

> Tailscale `.ts.net` 도메인은 `/.well-known/assetlinks.json` 을 못 올리므로
> TWA 상단에 얇은 도메인 바가 보일 수 있음(동작엔 지장 없음). 완전 제거하려면
> 사내 도메인 + 리버스프록시가 필요.

**Bubblewrap (로컬 CLI)** — `README.md` 의 C 항목 참고. JDK+SDK ~1GB 자동 다운로드.

---

## 요약

| 목적 | 방법 |
|---|---|
| 폰에 앱 아이콘 + 전체화면 (사내외 모두) | 1~2번 + 3-A |
| 사내 MDM 대량 배포 / 스토어 등록 | 3-B (PWABuilder) |
