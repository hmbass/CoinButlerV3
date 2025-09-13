#!/bin/bash

# CoinButler V3 시스템 진단 스크립트
# 서버 환경에서 발생할 수 있는 문제들을 미리 체크

set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 로그 함수
log_info() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_check() {
    echo -e "${BLUE}[?]${NC} $1"
}

# 진단 결과 저장
ISSUES=()
WARNINGS=()

echo "🔍 CoinButler V3 시스템 진단"
echo "=========================="

# 1. 시스템 환경 확인
log_check "시스템 환경 확인 중..."

echo "운영체제: $(uname -s) $(uname -r)"
echo "아키텍처: $(uname -m)"

# 메모리 확인
TOTAL_MEM=$(free -m | awk 'NR==2{printf "%.0f", $2}')
FREE_MEM=$(free -m | awk 'NR==2{printf "%.0f", $7}')

echo "메모리: ${FREE_MEM}MB / ${TOTAL_MEM}MB 사용 가능"

if [ "$FREE_MEM" -lt 500 ]; then
    WARNINGS+=("메모리 부족: ${FREE_MEM}MB (최소 500MB 권장)")
else
    log_info "메모리 충분"
fi

# 디스크 공간 확인
DISK_USAGE=$(df . | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 90 ]; then
    ISSUES+=("디스크 공간 부족: ${DISK_USAGE}% 사용 중")
else
    log_info "디스크 공간 충분"
fi

# 2. Python 환경 확인
log_check "Python 환경 확인 중..."

if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo "Python: $PYTHON_VERSION"
    
    # Python 버전 체크 (3.8 이상 권장)
    PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
    PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
    
    if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 8 ]; then
        log_info "Python 버전 적합"
    else
        WARNINGS+=("Python 버전이 낮습니다: $PYTHON_VERSION (3.8 이상 권장)")
    fi
else
    ISSUES+=("Python3가 설치되지 않음")
fi

# pip 확인
if command -v pip3 &> /dev/null; then
    PIP_VERSION=$(pip3 --version)
    echo "pip: $PIP_VERSION"
    log_info "pip3 사용 가능"
else
    ISSUES+=("pip3가 설치되지 않음")
fi

# 3. 가상환경 확인
log_check "가상환경 확인 중..."

if [ -d "venv" ]; then
    log_info "가상환경 존재"
    
    # 가상환경 활성화 테스트
    source venv/bin/activate 2>/dev/null && {
        VENV_PYTHON=$(which python)
        echo "가상환경 Python: $VENV_PYTHON"
        
        # 설치된 패키지 확인
        INSTALLED_COUNT=$(pip list 2>/dev/null | wc -l)
        echo "설치된 패키지 수: $INSTALLED_COUNT"
        
        # 핵심 패키지 확인
        MISSING_CORE=()
        pip show requests &>/dev/null || MISSING_CORE+=("requests")
        pip show pandas &>/dev/null || MISSING_CORE+=("pandas")
        pip show schedule &>/dev/null || MISSING_CORE+=("schedule")
        
        if [ ${#MISSING_CORE[@]} -gt 0 ]; then
            WARNINGS+=("핵심 패키지 누락: ${MISSING_CORE[*]}")
        else
            log_info "핵심 패키지 설치됨"
        fi
        
        deactivate
    } || {
        ISSUES+=("가상환경 활성화 실패")
    }
else
    WARNINGS+=("가상환경이 생성되지 않음")
fi

# 4. 네트워크 연결 확인
log_check "네트워크 연결 확인 중..."

# PyPI 연결
if ping -c 1 pypi.org &>/dev/null; then
    log_info "PyPI 연결 정상"
else
    WARNINGS+=("PyPI 연결 불안정 - 패키지 설치 시 문제 발생 가능")
fi

# 업비트 API 연결
if ping -c 1 api.upbit.com &>/dev/null; then
    log_info "업비트 API 연결 정상"
else
    WARNINGS+=("업비트 API 연결 불안정")
fi

# DNS 확인
if nslookup google.com &>/dev/null; then
    log_info "DNS 해상도 정상"
else
    ISSUES+=("DNS 해상도 실패")
fi

# 5. 포트 확인
log_check "포트 사용 현황 확인 중..."

# 8501 포트 (Streamlit)
if command -v netstat &>/dev/null; then
    if netstat -tuln | grep -q ":8501"; then
        WARNINGS+=("포트 8501이 이미 사용 중 - 대시보드 충돌 가능")
    else
        log_info "포트 8501 사용 가능"
    fi
elif command -v ss &>/dev/null; then
    if ss -tuln | grep -q ":8501"; then
        WARNINGS+=("포트 8501이 이미 사용 중 - 대시보드 충돌 가능")
    else
        log_info "포트 8501 사용 가능"
    fi
fi

# 6. 파일 시스템 권한 확인
log_check "파일 시스템 권한 확인 중..."

# 현재 디렉토리 쓰기 권한
if [ -w "." ]; then
    log_info "현재 디렉토리 쓰기 가능"
else
    ISSUES+=("현재 디렉토리 쓰기 권한 없음")
fi

# 로그 디렉토리 생성 가능 여부
if mkdir -p logs 2>/dev/null; then
    log_info "로그 디렉토리 생성 가능"
else
    ISSUES+=("로그 디렉토리 생성 실패")
fi

# 실행 권한 확인
if [ -x "start.sh" ]; then
    log_info "start.sh 실행 권한 있음"
else
    WARNINGS+=("start.sh 실행 권한 없음 - chmod +x start.sh 실행 필요")
fi

# 7. 환경 설정 파일 확인
log_check "환경 설정 파일 확인 중..."

if [ -f ".env" ]; then
    log_info ".env 파일 존재"
    
    # 필수 환경변수 확인
    MISSING_VARS=()
    grep -q "UPBIT_ACCESS_KEY=" .env || MISSING_VARS+=("UPBIT_ACCESS_KEY")
    grep -q "UPBIT_SECRET_KEY=" .env || MISSING_VARS+=("UPBIT_SECRET_KEY")
    grep -q "GEMINI_API_KEY=" .env || MISSING_VARS+=("GEMINI_API_KEY")
    
    if [ ${#MISSING_VARS[@]} -gt 0 ]; then
        WARNINGS+=("필수 환경변수 누락: ${MISSING_VARS[*]}")
    else
        log_info "필수 환경변수 설정됨"
    fi
else
    WARNINGS+=(".env 파일 없음 - API 키 설정 필요")
fi

if [ -f "config.env" ]; then
    log_info "config.env 템플릿 존재"
else
    WARNINGS+=("config.env 템플릿 파일 없음")
fi

# 8. 시스템 리소스 모니터링
log_check "시스템 리소스 확인 중..."

# CPU 사용률 (5초 평균)
if command -v top &>/dev/null; then
    CPU_USAGE=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | awk -F% '{print $1}')
    if [ -n "$CPU_USAGE" ]; then
        echo "CPU 사용률: ${CPU_USAGE}%"
        if (( $(echo "$CPU_USAGE > 80" | bc -l) 2>/dev/null )); then
            WARNINGS+=("CPU 사용률 높음: ${CPU_USAGE}%")
        fi
    fi
fi

# Load Average
if command -v uptime &>/dev/null; then
    LOAD_AVG=$(uptime | awk -F'load average:' '{print $2}')
    echo "Load Average:$LOAD_AVG"
fi

# 9. 결과 요약
echo ""
echo "==============================="
echo "🔍 진단 결과 요약"
echo "==============================="

if [ ${#ISSUES[@]} -gt 0 ]; then
    echo -e "${RED}❌ 심각한 문제 (${#ISSUES[@]}개):${NC}"
    for issue in "${ISSUES[@]}"; do
        echo -e "${RED}  ▸${NC} $issue"
    done
    echo ""
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo -e "${YELLOW}⚠️ 주의사항 (${#WARNINGS[@]}개):${NC}"
    for warning in "${WARNINGS[@]}"; do
        echo -e "${YELLOW}  ▸${NC} $warning"
    done
    echo ""
fi

if [ ${#ISSUES[@]} -eq 0 ] && [ ${#WARNINGS[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ 모든 검사 통과!${NC}"
    echo "시스템이 CoinButler V3 실행에 적합합니다."
fi

# 10. 권장사항 출력
echo "==============================="
echo "🔧 권장사항"
echo "==============================="

if [ ${#ISSUES[@]} -gt 0 ]; then
    echo "1. 심각한 문제들을 먼저 해결해주세요."
    echo "2. ./install_server.sh 스크립트를 실행해보세요."
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo "3. 주의사항들을 검토해주세요."
    echo "4. API 키 설정: nano .env"
fi

echo "5. 시스템 모니터링: htop 또는 top 명령어 사용"
echo "6. 로그 확인: tail -f logs/coinbutler_main.log"
echo "7. 정기적인 시스템 업데이트 권장"

# 종료 코드 설정
if [ ${#ISSUES[@]} -gt 0 ]; then
    exit 1
elif [ ${#WARNINGS[@]} -gt 0 ]; then
    exit 2  # 경고만 있는 경우
else
    exit 0  # 모든 검사 통과
fi
