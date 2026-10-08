# 컴프레셔 원격 모니터링/제어 (프로토타입)

100HP 컴프레셔 9대(미쓰비시 PLC 가정)를 통합 원격 모니터링/제어하는 프로토타입.
현재 스펙상 phase 1(현장조사)이 진행 중이라 실제 PLC 는 미연결 상태이므로,
**가상 PLC 시뮬레이터**를 포함해 전체 데이터 흐름이 실제와 동일하게 동작하도록 구성했다.

```
[가상 PLC 9대 (simulator)]  --mini-SLMP(TCP)-->  [수집 게이트웨이 (gateway)]
                                                       |  SQLite 시계열 저장
                                                       |  알람 판정 / 제어명령 실행
                                                       v
                                              [Flask 웹앱 + REST API]
                                                       v
                                        [모바일 대응 웹 대시보드 (PWA)]
```

> **같은 저장소의 별도 프로그램 — SAM4S AI 회의록**: 음성 녹음 → 음성인식 → AI 회의록 → Action Item 관리.
> `AI회의록_실행.bat` 더블클릭. 설명은 [`meeting_minutes/사용설명서.md`](meeting_minutes/사용설명서.md).

## 빠른 실행 (Windows)

`실행.bat` 더블클릭 → 최초 1회 가상환경 생성 + 패키지 설치 후 자동 실행.

수동:
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

브라우저에서 <http://127.0.0.1:8070> 접속.

### PC 로그인 시 자동 실행 (상시 서버)

`server\상시실행_설치.bat` 더블클릭(관리자 권한) → Windows 예약 작업으로 등록되어
로그인 시 자동 실행 + 죽으면 자동 재시작 + 방화벽 8070 포트 허용.
해제는 `server\상시실행_제거.bat`, 상태 확인은 `server\서버_상태확인.bat`.
자세한 내용은 `server\사용법.txt`.

### 핸드폰에 앱으로 설치 + QR 진입

- **QR 진입**: 서버 실행 후 폰으로 `http://<PC-IP>:8070/join` 접속 → QR + 설치 안내.
  QR 은 접속 경로에 맞춰 자동으로 맞는 주소를 가리킴. 벽부착용 PNG 는 `scripts\make_qr.py`.
- **사내외 모두 + 앱 설치**: `build-apk\TAILSCALE_APK.md` 절차대로 —
  Tailscale 설치 → `server\https_tailscale.bat` 로 HTTPS 주소 → 폰에서 "앱 설치"(안드로이드 WebAPK) / "홈 화면에 추가"(iOS)
- **.apk 파일이 필요하면**(MDM/스토어): `build-apk\README.md` (PWABuilder 또는 Bubblewrap),
  `scripts\set_apk_host.py <host>` 로 `twa-manifest.json` 자동 작성
- 아이콘 재생성: `.venv\Scripts\python.exe scripts\gen_icons.py` (pillow)

| 계정 | 비밀번호 | 권한 |
|---|---|---|
| admin | admin1234 | 전체 + 압력설정 + 명령승인 + 사용자관리 |
| operator | operator1234 | 기동/정지 명령(관리자 승인 후 실행) |
| viewer | viewer1234 | 모니터링만 |

> 최초 로그인 시 비밀번호 변경 화면으로 이동. 운영 전 `config.yaml` 의 `server.secret_key` 도 변경.

## 구성 요소

| 경로 | 설명 |
|---|---|
| `config.yaml` | 유닛 목록, D 레지스터 맵, 임계값, 누설감지 파라미터, 알림 설정 |
| `simulator/plc_sim.py` | 컴프레셔 물리모델(압력·전류·온도·가동시간) + 유닛별 mini-SLMP TCP 서버 |
| `gateway/protocol.py` | SLMP 3E 바이너리 프레임 빌더/파서(배치 리드 0x0401 / 라이트 0x1401) |
| `gateway/client.py` | 게이트웨이 측 SLMP 클라이언트(실 PLC/시뮬레이터 공통) |
| `gateway/poller.py` | 9대 폴링 → `readings` 저장 → 알람 평가 → 승인된 명령 PLC 기록 |
| `core/db.py` | SQLite 스키마(readings/alarms/commands/users/audit/notifications) |
| `core/alarms.py` | FAULT / 고온 / 과전류 / 통신두절 / **야간·휴일 누설의심 자동감지** |
| `core/control.py` | 제어명령 큐 + 관리자 승인 워크플로 + 감사로그 |
| `core/auth.py` | 세션 로그인, 역할 3단계, 선택적 TOTP 2FA |
| `web/app.py` | Flask 라우트(페이지 + REST API) |
| `web/templates`, `web/static` | 대시보드 / 유닛상세(1h·24h·7d 트렌드) / 알람 / 관리자 + PWA |
| `server/` | 상시 실행 스크립트(예약 작업 등록/해제, 감시 재시작, 상태 확인) |

현황판 상단 KPI: 가동 대수, **운영 마력(가동중 HP / 전체 HP)**, 운영 출력(kW), 통신두절, 발생 알람.
1대 정격은 `config.yaml` 의 `equipment` (기본 100 HP / 74.6 kW).

## 수집 태그 (스펙 반영)

| 태그 | 타입 | mini-SLMP D주소 | 비고 |
|---|---|---|---|
| RUN_STATUS | bool | D100 | 가동/정지 |
| DISCHARGE_PRESSURE | float | D101 | 값/1000 = MPa |
| MOTOR_CURRENT | float | D102 | 값/10 = A |
| FAULT_ALARM | bool | D103 | |
| DISCHARGE_TEMP | float | D104 | 값/10 = ℃ |
| RUN_HOURS_ACCUM | int | D105/D106 | 32비트 hr |

제어: `D200` 기동, `D201` 정지, `D202` 압력설정값(×1000).

## 실제 PLC 연결 시

1. `config.yaml` → `simulator.enabled: false`
2. `units[].host` = 각 PLC 실 IP, `units[].port` = PLC SLMP 포트
3. `devices` 의 D주소 / `scaling` 배율을 실제 래더 태그에 맞게 조정
4. 장비별로 SLMP 프레임 종류(3E/4E), 비트 디바이스 표기, 디바이스 코드만
   `gateway/protocol.py` 에서 맞추면 나머지 코드는 그대로 사용
5. `python run.py --no-sim`

> `gateway/protocol.py` 의 mini-SLMP 는 실제 SLMP 3E 바이너리와 헤더/커맨드 구조를
> 동일하게 맞춘 학습·프로토타입용 축약 구현이다. 실장비 검증은 현장에서 필요.

## 스펙 대비 진행 상황

- [x] 9대 통합 현황판(가동/정지, 압력, 알람), 24h/7d 트렌드
- [x] 개별 기동/정지(권한별 승인), 압력 설정값 변경(admin)
- [x] 이상 발생 시 앱 내 알림 + SMS(스텁: DB/콘솔 기록, 실제 게이트웨이 미연동)
- [x] 야간/휴일 무부하 압력강하 누설 의심 자동감지
- [x] 감사로그, OT/게이트웨이만 노출하는 구조(웹앱↔게이트웨이↔PLC 분리)
- [ ] 실 PLC 연동, VPN 게이트웨이 반입, 대수운전 최적화, 전력량계 연동 (phase 3~4)

## 아직 확정 필요 (스펙 open_items)

- 미쓰비시 PLC 정확한 모델명 및 Ethernet 유닛 장착 여부
- M2I TOP HMI 모델명(이더넷 지원 여부)
- 9대 배관 연결 구조(개별 vs 공용 헤더) — 대수운전 로직 설계에 필요
- 사내 네트워크 보안정책(VPN 게이트웨이 반입 승인)
