# 🚀 CoinButlerV3 설치 및 실행 가이드

## 📋 **사전 준비사항**

### 1. 시스템 요구사항
- **운영체제**: Linux, macOS
- **Python**: 3.8 이상
- **메모리**: 최소 1GB RAM 권장
- **디스크**: 500MB 여유 공간
- **네트워크**: 안정적인 인터넷 연결

### 2. API 키 준비
다음 API 키들을 미리 준비해주세요:

#### 필수 API 키
- **업비트 API 키**: [업비트 Open API](https://upbit.com/mypage/open_api_management)
  - Access Key, Secret Key 필요
  - 자산 조회, 주문 권한 필요
- **Google Gemini API 키**: [Google AI Studio](https://aistudio.google.com/)
  - 무료 사용량: 월 15회 요청

#### 선택적 API 키  
- **텔레그램 봇 토큰**: [@BotFather](https://t.me/botfather)에서 봇 생성
- **텔레그램 채팅 ID**: [@userinfobot](https://t.me/userinfobot)에서 확인

---

## ⚙️ **설치 및 설정**

### 1단계: 프로젝트 클론
```bash
git clone <repository-url>
cd CoinButlerV3
```

### 2단계: 환경 설정
```bash
# 환경 설정 파일 복사
cp config.env .env

# API 키 설정 (필수!)
nano .env
```

### 3단계: 실행 권한 부여
```bash
chmod +x start.sh stop.sh status.sh
```

---

## 🔧 **환경변수 설정**

`.env` 파일에서 다음 값들을 설정해주세요:

### 🔑 **필수 설정**
```bash
# 업비트 API (필수)
UPBIT_ACCESS_KEY=your_actual_access_key
UPBIT_SECRET_KEY=your_actual_secret_key

# Gemini AI API (필수)  
GEMINI_API_KEY=your_actual_gemini_key
```

### 💰 **거래 설정**
```bash
# 투자 금액 (권장: 10만원 이상)
INVESTMENT_AMOUNT=100000

# 수익률 (3% 권장)
PROFIT_RATE=0.03

# 손절률 (-2% 권장)  
LOSS_RATE=-0.02

# 🎯 NEW: 매수 대상 종목 수 (거래대금 기준 상위 N개)
TOP_MARKET_LIMIT=10

# 🕰️ NEW: 매일 정시 매도 시간 (24시간 형식)
DAILY_SELL_TIME=08:00

# 🕰️ NEW: 매일 정시 매도 활성화
ENABLE_DAILY_SELL=True
```

### 🛡️ **리스크 관리**
```bash
# 최대 포지션 수 (권장: 3개)
MAX_POSITIONS=3

# 일일 손실 한도 (권장: -5만원)
DAILY_LOSS_LIMIT=-50000
```

---

## 🚀 **실행 방법**

### 기본 실행 (백그라운드)
```bash
./start.sh
```

### 다양한 실행 옵션
```bash
# 전경에서 실행 (로그 실시간 확인)
./start.sh -f

# 대시보드만 실행
./start.sh -d

# 봇만 실행 (거래만)
./start.sh --bot-only

# 상태 확인
./start.sh -s
```

### 시스템 제어
```bash
# 상태 확인
./status.sh

# 시스템 중지
./stop.sh

# 로그 확인
tail -f logs/coinbutler.log
```

---

## 📊 **대시보드 접속**

시스템 시작 후 웹 브라우저에서 접속:
- **로컬**: http://localhost:8501
- **외부**: http://[서버IP]:8501

### 대시보드 기능
- 📈 실시간 포지션 현황
- 💰 손익 현황 및 통계
- 🎯 AI 분석 결과
- ⚙️ 설정 변경

---

## ⚠️ **주의사항**

### 🔒 **보안**
- `.env` 파일은 절대 공유하지 마세요
- API 키는 정기적으로 갱신하세요
- 업비트 API는 IP 화이트리스트 설정 권장

### 💸 **투자 위험**
- **소액 테스트**: 처음에는 최소 금액으로 테스트
- **지속 모니터링**: 정기적으로 시스템 상태 확인
- **손실 한도**: 일일 손실 한도를 반드시 설정
- **백업**: 중요한 거래 데이터는 정기 백업

### 🔧 **기술적 주의사항**
- 가상환경이 자동으로 생성됩니다
- 로그 파일은 `logs/` 폴더에 저장됩니다
- 포지션 정보는 `positions.json`에 저장됩니다

---

## 🆘 **문제해결**

### 자주 발생하는 문제

#### 1. API 연결 실패
```bash
# .env 파일 API 키 재확인
cat .env | grep -E "(UPBIT|GEMINI)"

# 네트워크 연결 확인
curl -s https://api.upbit.com/v1/market/all
```

#### 2. 권한 오류
```bash
# 실행 권한 부여
chmod +x *.sh

# 로그 폴더 권한 확인
mkdir -p logs
```

#### 3. 포트 충돌 (8501)
```bash
# 포트 사용 확인
lsof -i :8501

# 다른 포트 사용
export STREAMLIT_PORT=8502
./start.sh
```

#### 4. 메모리 부족
```bash
# 메모리 사용량 확인
free -m

# 불필요한 프로세스 종료 후 재시작
./stop.sh
./start.sh
```

---

## 📞 **지원**

### 로그 확인
```bash
# 에러 로그
tail -f logs/coinbutler_error.log

# 일반 로그  
tail -f logs/coinbutler.log

# 통합 로그
tail -f coinbutler_main.log
```

### 시스템 정보
```bash
# Python 버전
python3 --version

# 의존성 확인
pip list

# 디스크 공간
df -h
```

---

## 🆕 **V3 신규 기능**

### 🎯 **거래대금 TOP10 매수 전략**
- **기존**: 거래량 급등 감지 후 종목 선택
- **개선**: 전일자 기준 거래대금 상위 10개 종목만 매수 대상
- **장점**: 
  - 유동성이 높은 종목만 거래 → 슬리피지 최소화
  - 시장 관심도가 높은 종목 → 수익률 향상
  - API 호출 최소화 → 시스템 효율성 증대

### 🕰️ **매일 정시 매도 스케줄링**
- **기능**: 매일 오전 8시에 모든 보유 종목 자동 매도
- **목적**: 
  - 장기 보유 방지 → 리스크 관리
  - 매일 초기화 → 신선한 포트폴리오
  - 손절/익절 기준과 무관한 강제 청산
- **설정**: `DAILY_SELL_TIME`과 `ENABLE_DAILY_SELL`로 제어 가능

## 🎯 **성공적인 운영을 위한 팁**

1. **점진적 시작**: 소액으로 시작해서 시스템 신뢰도 확보
2. **정기 점검**: 매일 대시보드에서 성과 확인  
3. **설정 조정**: 시장 상황에 따른 파라미터 조정
4. **백업 습관**: 중요한 설정과 데이터 정기 백업
5. **업데이트 확인**: 새로운 기능 및 보안 패치 적용
6. **🆕 정시 매도**: 오전 8시 전량 매도로 리스크 관리 강화

**⚠️ 투자에는 항상 위험이 따릅니다. 본인의 투자 능력과 위험 감수 능력을 충분히 고려하여 사용하세요.**
