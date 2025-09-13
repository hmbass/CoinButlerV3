#!/bin/bash

# CoinButler V3 서버 환경 설치 스크립트
# 네트워크 연결이 불안정한 서버 환경에 최적화

set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 로그 함수
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# 변수 설정
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "🤖 CoinButler V3 서버 환경 설치"
echo "==============================="

# 1. 시스템 요구사항 확인
log_step "시스템 요구사항 확인 중..."

# Python 3 확인
if ! command -v python3 &> /dev/null; then
    log_error "Python3가 설치되지 않았습니다."
    log_info "Ubuntu/Debian: sudo apt-get install python3 python3-pip python3-venv"
    log_info "CentOS/RHEL: sudo yum install python3 python3-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
log_info "Python 버전: $PYTHON_VERSION"

# pip 확인
if ! command -v pip3 &> /dev/null; then
    log_warn "pip3가 없습니다. 설치 중..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y python3-pip
    elif command -v yum &> /dev/null; then
        sudo yum install -y python3-pip
    else
        log_error "패키지 매니저를 찾을 수 없습니다. 수동으로 pip3를 설치해주세요."
        exit 1
    fi
fi

# 2. 네트워크 연결 테스트
log_step "네트워크 연결 테스트 중..."

if ping -c 1 pypi.org &> /dev/null; then
    log_info "PyPI 연결 정상"
    USE_MIRROR=false
else
    log_warn "PyPI 연결 불안정 - 미러 서버 사용"
    USE_MIRROR=true
fi

# 3. 가상환경 생성
log_step "Python 가상환경 설정 중..."

if [ -d "venv" ]; then
    log_info "기존 가상환경 발견 - 재사용"
else
    python3 -m venv venv
    log_info "가상환경 생성 완료"
fi

# 4. 가상환경 활성화
source venv/bin/activate
log_info "가상환경 활성화됨"

# 5. pip 업그레이드
log_step "pip 업그레이드 중..."

if [ "$USE_MIRROR" = true ]; then
    pip install --upgrade pip -i https://pypi.python.org/simple/ --trusted-host pypi.python.org --timeout=120
else
    pip install --upgrade pip --timeout=60 --retries=5
fi

# 6. 의존성 설치 (강화된 오류 처리)
log_step "의존성 패키지 설치 중..."

# 핵심 패키지 목록 (우선 설치)
CORE_PACKAGES=(
    "requests>=2.31.0"
    "python-dotenv>=1.0.0"
    "schedule>=1.2.0"
    "pandas>=2.2.0"
    "numpy>=1.26.0"
)

# 선택 패키지 목록 (나중에 설치)
OPTIONAL_PACKAGES=(
    "streamlit>=1.28.0"
    "pyupbit>=0.2.31"
    "PyJWT>=2.8.0"
    "python-telegram-bot>=20.5"
    "google-generativeai>=0.3.0"
)

# 핵심 패키지 설치
log_info "핵심 패키지 설치 중..."
for package in "${CORE_PACKAGES[@]}"; do
    echo "  설치 중: $package"
    if [ "$USE_MIRROR" = true ]; then
        pip install "$package" -i https://pypi.python.org/simple/ --trusted-host pypi.python.org --timeout=120 --retries=3 --quiet || {
            log_warn "핵심 패키지 설치 실패: $package"
            exit 1
        }
    else
        pip install "$package" --timeout=60 --retries=5 --quiet || {
            log_warn "핵심 패키지 설치 실패: $package"
            # 미러로 재시도
            pip install "$package" -i https://pypi.python.org/simple/ --trusted-host pypi.python.org --timeout=120 --retries=3 --quiet || {
                log_error "핵심 패키지 설치 최종 실패: $package"
                exit 1
            }
        }
    fi
done

# 선택 패키지 설치
log_info "선택 패키지 설치 중..."
for package in "${OPTIONAL_PACKAGES[@]}"; do
    echo "  설치 중: $package"
    if [ "$USE_MIRROR" = true ]; then
        pip install "$package" -i https://pypi.python.org/simple/ --trusted-host pypi.python.org --timeout=120 --retries=3 --quiet || {
            log_warn "선택 패키지 설치 실패: $package (계속 진행)"
        }
    else
        pip install "$package" --timeout=60 --retries=3 --quiet || {
            log_warn "선택 패키지 설치 실패: $package - 미러로 재시도"
            pip install "$package" -i https://pypi.python.org/simple/ --trusted-host pypi.python.org --timeout=120 --retries=3 --quiet || {
                log_warn "선택 패키지 설치 최종 실패: $package (계속 진행)"
            }
        }
    fi
done

# 7. 설치 결과 확인
log_step "설치 결과 확인 중..."

INSTALLED_PACKAGES=$(pip list --format=freeze | wc -l)
log_info "설치된 패키지 수: $INSTALLED_PACKAGES"

# 핵심 패키지 확인
MISSING_PACKAGES=()

if ! pip show requests &> /dev/null; then MISSING_PACKAGES+=("requests"); fi
if ! pip show python-dotenv &> /dev/null; then MISSING_PACKAGES+=("python-dotenv"); fi
if ! pip show schedule &> /dev/null; then MISSING_PACKAGES+=("schedule"); fi
if ! pip show pandas &> /dev/null; then MISSING_PACKAGES+=("pandas"); fi
if ! pip show numpy &> /dev/null; then MISSING_PACKAGES+=("numpy"); fi

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    log_error "핵심 패키지 누락: ${MISSING_PACKAGES[*]}"
    log_error "설치를 다시 시도하거나 수동으로 설치해주세요."
    exit 1
fi

# 8. 환경 설정 파일 생성
log_step "환경 설정 파일 확인 중..."

if [ ! -f ".env" ]; then
    if [ -f "config.env" ]; then
        cp config.env .env
        log_info ".env 파일 생성됨"
        log_warn "API 키를 설정해주세요: nano .env"
    else
        log_warn "config.env 파일을 찾을 수 없습니다."
    fi
else
    log_info ".env 파일이 이미 존재합니다."
fi

# 9. 로그 디렉토리 생성
log_step "로그 디렉토리 생성 중..."
mkdir -p logs
chmod 755 logs

# 10. 실행 권한 부여
log_step "실행 권한 설정 중..."
chmod +x *.sh 2>/dev/null || true

# 11. 방화벽 확인 (포트 8501)
log_step "방화벽 설정 확인 중..."
if command -v ufw &> /dev/null; then
    if ufw status | grep -q "8501"; then
        log_info "포트 8501이 이미 열려있습니다."
    else
        log_warn "포트 8501이 닫혀있을 수 있습니다."
        log_info "대시보드 사용을 위해 포트를 열어주세요: sudo ufw allow 8501"
    fi
fi

# 12. 메모리 사용량 확인
log_step "시스템 리소스 확인 중..."
FREE_MEMORY=$(free -m | awk 'NR==2{printf "%.0f", $7}')
if [ "$FREE_MEMORY" -lt 500 ]; then
    log_warn "사용 가능한 메모리가 부족할 수 있습니다: ${FREE_MEMORY}MB"
    log_info "최소 500MB 이상의 여유 메모리를 권장합니다."
fi

# 완료
echo ""
log_info "🎉 CoinButler V3 서버 설치가 완료되었습니다!"
echo ""
log_info "다음 단계:"
log_info "1. API 키 설정: nano .env"
log_info "2. 시스템 시작: ./start.sh"
log_info "3. 상태 확인: ./status.sh"
echo ""
log_warn "⚠️ 실제 거래 전에 소액으로 테스트해보세요!"

# 가상환경 비활성화
deactivate
