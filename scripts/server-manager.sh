#!/bin/bash

# =============================================================================
# CoinButlerV3 통합 서버 관리 스크립트
# server.mdc 가이드 준수 - 필수 로그 점검 포함
# =============================================================================

set -e  # 오류 발생 시 스크립트 중단

# =============================================================================
# 설정 변수
# =============================================================================

PROJECT_ROOT="/Users/kevinpark/Desktop/Dev/CoinButlerV3"
VENV_PATH="$PROJECT_ROOT/venv"
PYTHON_CMD="$VENV_PATH/bin/python"

# PID 파일들
COINBUTLER_PID_FILE="$PROJECT_ROOT/coinbutler.pid"
DASHBOARD_PID_FILE="$PROJECT_ROOT/dashboard.pid"

# 로그 파일들
LOGS_DIR="$PROJECT_ROOT/logs"
COINBUTLER_LOG="$LOGS_DIR/coinbutler.log"
COINBUTLER_ERROR_LOG="$LOGS_DIR/coinbutler_error.log"
DASHBOARD_LOG="$LOGS_DIR/dashboard.log"
SYSTEM_LOG="$LOGS_DIR/system.log"

# 포트 설정
DASHBOARD_PORT=8501
COINBUTLER_PORT=8000  # API 포트 (필요시)

# 색상 코드
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# =============================================================================
# 유틸리티 함수들
# =============================================================================

log_message() {
    local level=$1
    local message=$2
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    case $level in
        "INFO")
            echo -e "${GREEN}[INFO]${NC} $message"
            echo "[$timestamp] [INFO] $message" >> "$SYSTEM_LOG"
            ;;
        "WARN")
            echo -e "${YELLOW}[WARN]${NC} $message"
            echo "[$timestamp] [WARN] $message" >> "$SYSTEM_LOG"
            ;;
        "ERROR")
            echo -e "${RED}[ERROR]${NC} $message"
            echo "[$timestamp] [ERROR] $message" >> "$SYSTEM_LOG"
            ;;
        "SUCCESS")
            echo -e "${GREEN}[SUCCESS]${NC} $message"
            echo "[$timestamp] [SUCCESS] $message" >> "$SYSTEM_LOG"
            ;;
    esac
}

# PID 파일로 프로세스 종료
stop_service_by_pid() {
    local pid_file=$1
    local service_name=$2
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            log_message "INFO" "$service_name 종료 중... (PID: $pid)"
            kill -TERM "$pid" 2>/dev/null || true
            sleep 3
            
            # 강제 종료가 필요한 경우
            if kill -0 "$pid" 2>/dev/null; then
                log_message "WARN" "$service_name 강제 종료 중..."
                kill -KILL "$pid" 2>/dev/null || true
                sleep 1
            fi
            
            if ! kill -0 "$pid" 2>/dev/null; then
                log_message "SUCCESS" "$service_name 종료 완료"
            else
                log_message "ERROR" "$service_name 종료 실패"
                return 1
            fi
        fi
        rm -f "$pid_file"
    fi
}

# 포트로 프로세스 종료
kill_process_by_port() {
    local port=$1
    local service_name=$2
    
    local pid=$(lsof -ti :$port 2>/dev/null || true)
    if [ -n "$pid" ]; then
        log_message "WARN" "포트 $port 에서 실행 중인 프로세스 종료: $pid"
        kill -TERM $pid 2>/dev/null || true
        sleep 2
        
        # 아직 살아있으면 강제 종료
        if kill -0 $pid 2>/dev/null; then
            kill -KILL $pid 2>/dev/null || true
        fi
        log_message "INFO" "$service_name 포트 정리 완료"
    fi
}

# 서비스 상태 확인
check_service_status() {
    local pid_file=$1
    local service_name=$2
    local port=$3
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            local port_status=""
            if [ "$port" != "N/A" ]; then
                if netstat -an | grep ":$port.*LISTEN" > /dev/null 2>&1; then
                    port_status="포트 $port ✅"
                else
                    port_status="포트 $port ❌"
                fi
            fi
            echo "  $service_name: 🟢 실행중 (PID: $pid) $port_status"
            return 0
        else
            echo "  $service_name: 🔴 중단됨 (PID 파일 존재하나 프로세스 없음)"
            rm -f "$pid_file"
            return 1
        fi
    else
        echo "  $service_name: 🔴 중단됨"
        return 1
    fi
}

# =============================================================================
# 헬스체크 및 오류 감지 시스템
# =============================================================================

# CoinButler 헬스체크
check_coinbutler_health() {
    # PID 기반 헬스체크
    if [ -f "$COINBUTLER_PID_FILE" ]; then
        local pid=$(cat "$COINBUTLER_PID_FILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# 대시보드 헬스체크
check_dashboard_health() {
    # Streamlit 포트 체크
    if netstat -an | grep ":$DASHBOARD_PORT.*LISTEN" > /dev/null 2>&1; then
        # HTTP 요청으로 실제 응답 확인
        if curl -s "http://localhost:$DASHBOARD_PORT" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# 치명적 오류 검사
check_for_critical_errors() {
    local logfile=$1
    local service_name=$2
    
    if [ -f "$logfile" ]; then
        # 최근 30줄에서 치명적 오류 검색
        local critical_errors=$(tail -n 30 "$logfile" | grep -i "error\|exception\|failed\|traceback\|fatal\|critical" | wc -l)
        local connection_errors=$(tail -n 30 "$logfile" | grep -i "connection.*failed\|database.*error\|network.*error\|api.*error" | wc -l)
        
        if [ "$critical_errors" -gt 0 ]; then
            log_message "WARN" "$service_name에서 $critical_errors개의 오류 메시지 발견"
            echo ""
            log_message "WARN" "최근 오류 내용:"
            tail -n 30 "$logfile" | grep -i "error\|exception\|failed\|traceback" | tail -n 3
            echo ""
            
            # 연결 오류는 치명적으로 분류
            if [ "$connection_errors" -gt 0 ]; then
                return 1  # 치명적 오류
            fi
        fi
    fi
    return 0  # 정상 또는 경미한 오류
}

# 서비스 시작 검증
validate_service_startup() {
    local service_name=$1
    local log_file=$2
    local port=$3
    local health_check_func=$4
    
    log_message "INFO" "$service_name 시작 후 자동 점검 수행 중..."
    
    # 1. 프로세스 실행 상태 확인
    sleep 3  # 서버 초기화 대기
    
    # 2. 포트 바인딩 확인 (포트가 지정된 경우)
    if [ "$port" != "N/A" ] && [ -n "$port" ]; then
        if ! netstat -an | grep ":$port.*LISTEN" > /dev/null 2>&1; then
            log_message "ERROR" "$service_name 포트 $port 바인딩 실패"
            return 1
        fi
    fi
    
    # 3. 헬스체크 엔드포인트 확인
    if ! $health_check_func; then
        log_message "ERROR" "$service_name 헬스체크 실패"
        return 1
    fi
    
    # 4. 로그에서 오류 키워드 검색
    if check_for_critical_errors "$log_file" "$service_name"; then
        log_message "ERROR" "$service_name 로그에서 치명적 오류 발견"
        return 1
    fi
    
    log_message "INFO" "$service_name 자동 점검 완료 ✅"
    return 0
}

# 수동 점검 가이드 표시
show_manual_check_guide() {
    local service_name=$1
    local log_file=$2
    
    echo ""
    log_message "INFO" "📋 $service_name 수동 점검 체크리스트"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "   ✅ 다음 명령으로 로그를 확인하고 각 항목을 검증하세요:"
    echo "   📄 $0 logs $service_name"
    echo ""
    echo "   🔍 필수 확인 항목:"
    echo "   1️⃣  애플리케이션 초기화 완료 메시지 확인"
    echo "   2️⃣  API 연결 성공 로그 확인 (업비트, Gemini)" 
    echo "   3️⃣  설정 파일 로딩 성공 확인"
    echo "   4️⃣  텔레그램 알림 서비스 연결 상태 확인"
    echo "   5️⃣  환경변수 설정 정상 로딩 확인"
    echo "   6️⃣  메모리/디스크 사용량 정상 범위 확인"
    echo "   7️⃣  워닝 메시지가 있다면 무시 가능한 수준인지 판단"
    echo ""
    echo "   ⚠️  문제 발견 시 즉시 서버를 중단하고 문제를 해결하세요!"
    echo "   🛑 $0 stop"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

# 시작 검증 가이드 표시
show_startup_verification_guide() {
    echo ""
    log_message "INFO" "🎯 서버 시작 완료! 다음 단계를 수행하세요:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "   📋 1단계: 각 서비스 로그를 수동으로 점검하세요"
    echo "   👉 $0 logs coinbutler"
    echo "   👉 $0 logs dashboard"
    echo ""
    echo "   🔍 2단계: 브라우저에서 서비스 접속 테스트"
    echo "   🌐 Dashboard: http://localhost:$DASHBOARD_PORT"
    echo ""
    echo "   ⚠️  3단계: 문제 발견 시 즉시 중단하고 로그를 분석하세요"
    echo "   🛑 $0 stop"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

# =============================================================================
# 서비스 시작/중지 함수들
# =============================================================================

# CoinButler 봇 시작
start_coinbutler() {
    log_message "INFO" "CoinButler 봇 시작 중..."
    kill_process_by_port $COINBUTLER_PORT "CoinButler"
    
    # 가상환경 활성화 확인
    if [ ! -d "$VENV_PATH" ]; then
        log_message "ERROR" "가상환경을 찾을 수 없습니다: $VENV_PATH"
        return 1
    fi
    
    # 로그 디렉토리 생성
    mkdir -p "$LOGS_DIR"
    
    # CoinButler 봇 실행
    cd "$PROJECT_ROOT"
    nohup "$PYTHON_CMD" main.py bot > "$COINBUTLER_LOG" 2> "$COINBUTLER_ERROR_LOG" &
    echo $! > "$COINBUTLER_PID_FILE"
    
    sleep 3
    if validate_service_startup "CoinButler" "$COINBUTLER_LOG" "N/A" "check_coinbutler_health"; then
        log_message "SUCCESS" "CoinButler 봇 시작 완료"
        show_manual_check_guide "CoinButler" "$COINBUTLER_LOG"
        return 0
    else
        log_message "ERROR" "CoinButler 봇 시작 실패"
        stop_service_by_pid "$COINBUTLER_PID_FILE" "CoinButler"
        return 1
    fi
}

# 대시보드 시작
start_dashboard() {
    log_message "INFO" "대시보드 시작 중..."
    kill_process_by_port $DASHBOARD_PORT "Dashboard"
    
    # 가상환경 활성화 확인
    if [ ! -d "$VENV_PATH" ]; then
        log_message "ERROR" "가상환경을 찾을 수 없습니다: $VENV_PATH"
        return 1
    fi
    
    # 로그 디렉토리 생성
    mkdir -p "$LOGS_DIR"
    
    # Streamlit 대시보드 실행
    cd "$PROJECT_ROOT"
    nohup "$PYTHON_CMD" -m streamlit run dashboard.py --server.port $DASHBOARD_PORT --server.address 0.0.0.0 > "$DASHBOARD_LOG" 2>&1 &
    echo $! > "$DASHBOARD_PID_FILE"
    
    sleep 5  # Streamlit 초기화 대기
    if validate_service_startup "Dashboard" "$DASHBOARD_LOG" "$DASHBOARD_PORT" "check_dashboard_health"; then
        log_message "SUCCESS" "대시보드 시작 완료 (포트: $DASHBOARD_PORT)"
        show_manual_check_guide "Dashboard" "$DASHBOARD_LOG"
        return 0
    else
        log_message "ERROR" "대시보드 시작 실패"
        stop_service_by_pid "$DASHBOARD_PID_FILE" "Dashboard"
        return 1
    fi
}

# 모든 서비스 시작
start_all_services() {
    log_message "INFO" "=== CoinButler 시스템 시작 ==="
    
    # 환경 변수 확인
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        log_message "ERROR" ".env 파일이 없습니다. env_example.txt를 참고하여 설정하세요."
        return 1
    fi
    
    local failed_services=()
    
    # CoinButler 봇 시작
    if ! start_coinbutler; then
        failed_services+=("CoinButler")
    fi
    
    # 대시보드 시작
    if ! start_dashboard; then
        failed_services+=("Dashboard")
    fi
    
    # 실패한 서비스가 있으면 전체 중단
    if [ ${#failed_services[@]} -gt 0 ]; then
        log_message "ERROR" "다음 서비스에서 문제 발견: ${failed_services[*]}"
        log_message "ERROR" "전체 시스템을 중단합니다."
        stop_all_services
        return 1
    fi
    
    log_message "SUCCESS" "=== CoinButler 시스템 시작 완료 ==="
    
    # 시작 검증 가이드 표시
    show_startup_verification_guide
    
    show_status
    return 0
}

# 모든 서비스 중지
stop_all_services() {
    log_message "INFO" "=== CoinButler 시스템 종료 ==="
    
    # 모든 서비스 순차적 종료
    stop_service_by_pid "$DASHBOARD_PID_FILE" "Dashboard"
    stop_service_by_pid "$COINBUTLER_PID_FILE" "CoinButler"
    
    log_message "SUCCESS" "=== CoinButler 시스템 종료 완료 ==="
}

# 모든 서비스 재시작
restart_all_services() {
    log_message "INFO" "=== CoinButler 시스템 재시작 ==="
    stop_all_services
    sleep 3  # 모든 프로세스 완전 종료 대기
    start_all_services
}

# 서비스 상태 확인
show_status() {
    echo ""
    log_message "INFO" "=== CoinButler 시스템 상태 ==="
    
    check_service_status "$COINBUTLER_PID_FILE" "CoinButler" "N/A"
    check_service_status "$DASHBOARD_PID_FILE" "Dashboard" "$DASHBOARD_PORT"
    
    echo ""
    log_message "INFO" "서비스 URL:"
    echo "  Dashboard: http://localhost:$DASHBOARD_PORT"
    echo ""
}

# 실시간 로그 모니터링
show_logs() {
    local service=$1
    
    case $service in
        "coinbutler"|"bot")
            log_message "INFO" "CoinButler 로그 모니터링 시작 (Ctrl+C로 종료)"
            tail -f "$COINBUTLER_LOG"
            ;;
        "dashboard"|"dash")
            log_message "INFO" "Dashboard 로그 모니터링 시작 (Ctrl+C로 종료)"
            tail -f "$DASHBOARD_LOG"
            ;;
        "error"|"err")
            log_message "INFO" "오류 로그 모니터링 시작 (Ctrl+C로 종료)"
            tail -f "$COINBUTLER_ERROR_LOG"
            ;;
        "system"|"sys"|"all"|"")
            log_message "INFO" "전체 시스템 로그 모니터링 시작 (Ctrl+C로 종료)"
            tail -f "$SYSTEM_LOG"
            ;;
        *)
            log_message "ERROR" "지원되지 않는 로그 타입: $service"
            echo "사용법: $0 logs [coinbutler|dashboard|error|system|all]"
            return 1
            ;;
    esac
}

# =============================================================================
# 메인 스크립트 로직
# =============================================================================

# 도움말 표시
show_help() {
    echo ""
    echo "CoinButlerV3 통합 서버 관리 스크립트"
    echo "server.mdc 가이드 준수 - 필수 로그 점검 포함"
    echo ""
    echo "사용법: $0 [명령어]"
    echo ""
    echo "📚 주요 명령어:"
    echo "  start                    모든 서비스 시작 (로그 점검 포함)"
    echo "  stop                     모든 서비스 안전 종료"
    echo "  restart                  모든 서비스 재시작"
    echo "  status                   서비스 상태 확인"
    echo ""
    echo "🔍 개별 서비스 관리:"
    echo "  start-coinbutler         CoinButler 봇만 시작"
    echo "  start-dashboard          대시보드만 시작"
    echo "  stop-coinbutler          CoinButler 봇만 중지"
    echo "  stop-dashboard           대시보드만 중지"
    echo ""
    echo "📋 로그 모니터링:"
    echo "  logs [서비스명]          실시간 로그 확인"
    echo "    - coinbutler, bot      CoinButler 봇 로그"
    echo "    - dashboard, dash      대시보드 로그"
    echo "    - error, err           오류 로그"
    echo "    - system, sys, all     전체 시스템 로그"
    echo ""
    echo "💡 기타:"
    echo "  help, -h, --help        이 도움말 표시"
    echo ""
    echo "🚨 중요: 서버 시작 후 반드시 로그를 점검하세요!"
    echo ""
}

# 로그 디렉토리 초기화
mkdir -p "$LOGS_DIR"

# 메인 명령어 처리
case "$1" in
    "start")
        start_all_services
        ;;
    "stop")
        stop_all_services
        ;;
    "restart")
        restart_all_services
        ;;
    "status")
        show_status
        ;;
    "start-coinbutler"|"start-bot")
        start_coinbutler
        ;;
    "start-dashboard"|"start-dash")
        start_dashboard
        ;;
    "stop-coinbutler"|"stop-bot")
        stop_service_by_pid "$COINBUTLER_PID_FILE" "CoinButler"
        ;;
    "stop-dashboard"|"stop-dash")
        stop_service_by_pid "$DASHBOARD_PID_FILE" "Dashboard"
        ;;
    "logs")
        show_logs "$2"
        ;;
    "help"|"-h"|"--help"|"")
        show_help
        ;;
    *)
        log_message "ERROR" "알 수 없는 명령어: $1"
        show_help
        exit 1
        ;;
esac
