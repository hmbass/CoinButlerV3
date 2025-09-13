"""
스케줄링 기능 - 매일 정해진 시간에 작업 실행
"""
import logging
import threading
import time
from datetime import datetime, time as dt_time
from typing import Callable, Dict, Any, Optional
import schedule as schedule_lib
from trade_utils import UpbitAPI
from risk_manager import RiskManager
from notifier import notify_sell, notify_info

def setup_integrated_logging():
    """통합 로깅 설정 (coinbutler_main.log 사용)"""
    logger = logging.getLogger(__name__)
    
    # 이미 핸들러가 있으면 제거
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # coinbutler_main.log에 로깅하도록 설정
    try:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler = logging.FileHandler('coinbutler_main.log', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        # 파일 생성 실패 시 콘솔 출력으로 대체
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logger.addHandler(console_handler)
    
    logger.setLevel(logging.INFO)
    return logger

logger = setup_integrated_logging()

class TradingScheduler:
    """거래 스케줄링 관리 클래스"""
    
    def __init__(self, upbit_api: UpbitAPI, risk_manager: RiskManager):
        self.upbit_api = upbit_api
        self.risk_manager = risk_manager
        self.is_running = False
        self.scheduler_thread = None
        
        # 스케줄 설정
        self._setup_schedules()
    
    def _setup_schedules(self):
        """스케줄 설정"""
        # 매일 오전 8시에 전량 매도 실행
        schedule_lib.every().day.at("08:00").do(self._daily_sell_all_positions)
        
        logger.info("📅 스케줄 설정 완료:")
        logger.info("  - 매일 08:00: 전량 매도")
    
    def _daily_sell_all_positions(self):
        """매일 오전 8시 전량 매도 실행"""
        try:
            logger.info("🕰️ 매일 정시 매도 시작 (08:00)")
            notify_info("📢 매일 정시 매도", "오전 8시 전량 매도를 시작합니다.")
            
            # 현재 보유 포지션 조회
            open_positions = self.risk_manager.get_open_positions()
            
            if not open_positions:
                logger.info("📭 매도할 포지션이 없습니다.")
                notify_info("📭 정시 매도 완료", "매도할 포지션이 없습니다.")
                return
            
            logger.info(f"📊 매도 대상 포지션: {len(open_positions)}개")
            
            sell_count = 0
            total_pnl = 0.0
            sell_results = []
            
            # 각 포지션 매도 실행
            for market, position in open_positions.items():
                try:
                    logger.info(f"🔄 {market} 매도 진행 중...")
                    
                    # 현재가 조회
                    current_price = self.upbit_api.get_current_price(market)
                    if not current_price:
                        logger.error(f"❌ {market} 현재가 조회 실패 - 매도 스킵")
                        continue
                    
                    # 보유 수량 확인
                    currency = market.split('-')[1]  # KRW-BTC -> BTC
                    coin_balance = self.upbit_api.get_coin_balance(currency)
                    
                    if coin_balance <= 0:
                        logger.warning(f"⚠️ {market} 보유 수량 없음 - 포지션 정리")
                        self.risk_manager.close_position(market, current_price)
                        continue
                    
                    # 매도 주문 실행
                    sell_result = self.upbit_api.place_sell_order(market, coin_balance)
                    
                    if sell_result:
                        # 손익 계산
                        pnl_info = self.risk_manager.get_position_pnl(market, current_price)
                        if pnl_info:
                            pnl, pnl_rate = pnl_info
                            total_pnl += pnl
                        else:
                            pnl, pnl_rate = 0, 0
                        
                        # 포지션 정리
                        self.risk_manager.close_position(market, current_price)
                        
                        sell_count += 1
                        sell_results.append({
                            'market': market,
                            'price': current_price,
                            'quantity': coin_balance,
                            'pnl': pnl,
                            'pnl_rate': pnl_rate
                        })
                        
                        logger.info(f"✅ {market} 매도 완료: {current_price:,.0f}원 × {coin_balance:.6f}")
                        
                        # 텔레그램 알림
                        amount = current_price * coin_balance
                        notify_sell(
                            market=market,
                            price=current_price,
                            amount=amount,
                            profit_loss=pnl,
                            profit_rate=pnl_rate,
                            reason="매일 정시 매도 (08:00)"
                        )
                        
                        # 매도 간격 (API 제한 방지)
                        time.sleep(2)
                    
                    else:
                        logger.error(f"❌ {market} 매도 주문 실패")
                
                except Exception as e:
                    logger.error(f"❌ {market} 매도 처리 오류: {e}")
                    continue
            
            # 매도 완료 결과 정리
            if sell_count > 0:
                logger.info(f"🎯 정시 매도 완료: {sell_count}개 종목, 총 손익: {total_pnl:,.0f}원")
                
                # 종합 결과 알림
                result_message = f"""
🎯 매일 정시 매도 완료 (08:00)

📊 매도 종목: {sell_count}개
💰 총 손익: {total_pnl:,.0f}원
📈 평균 수익률: {(total_pnl/len(sell_results) if sell_results else 0):+.2f}%

📋 상세 내역:"""
                
                for result in sell_results:
                    result_message += f"""
• {result['market']}: {result['pnl']:+,.0f}원 ({result['pnl_rate']:+.2f}%)"""
                
                notify_info("🎯 정시 매도 완료", result_message)
            
            else:
                logger.warning("⚠️ 매도된 포지션이 없습니다.")
                notify_info("⚠️ 정시 매도 오류", "매도 처리된 포지션이 없습니다.")
        
        except Exception as e:
            error_msg = f"매일 정시 매도 처리 중 오류 발생: {e}"
            logger.error(f"❌ {error_msg}")
            notify_info("❌ 정시 매도 오류", error_msg)
    
    def start(self):
        """스케줄러 시작"""
        if self.is_running:
            logger.warning("⚠️ 스케줄러가 이미 실행 중입니다.")
            return
        
        self.is_running = True
        
        # 별도 스레드에서 스케줄러 실행
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()
        
        logger.info("🕰️ 거래 스케줄러 시작됨")
        
        # 다음 스케줄 정보 출력
        next_run = schedule_lib.next_run()
        if next_run:
            logger.info(f"📅 다음 실행 예정: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    
    def stop(self):
        """스케줄러 중지"""
        self.is_running = False
        
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            logger.info("🛑 거래 스케줄러 중지 중...")
            # 스레드가 자연스럽게 종료될 때까지 잠시 대기
            time.sleep(1)
        
        # 스케줄 정리
        schedule_lib.clear()
        logger.info("🛑 거래 스케줄러 중지됨")
    
    def _run_scheduler(self):
        """스케줄러 메인 루프"""
        logger.info("🔄 스케줄러 루프 시작")
        
        while self.is_running:
            try:
                # 대기 중인 스케줄 실행
                schedule_lib.run_pending()
                
                # 1분마다 체크
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"❌ 스케줄러 루프 오류: {e}")
                time.sleep(60)  # 오류 후 1분 대기
        
        logger.info("🔄 스케줄러 루프 종료")
    
    def get_status(self) -> Dict[str, Any]:
        """스케줄러 상태 조회"""
        next_run = schedule_lib.next_run()
        
        return {
            'running': self.is_running,
            'thread_alive': self.scheduler_thread.is_alive() if self.scheduler_thread else False,
            'next_run': next_run.isoformat() if next_run else None,
            'scheduled_jobs': len(schedule_lib.jobs),
            'jobs': [
                {
                    'job': str(job.job_func),
                    'next_run': job.next_run.isoformat() if job.next_run else None
                }
                for job in schedule_lib.jobs
            ]
        }
    
    def is_daily_sell_time(self) -> bool:
        """현재 시간이 매일 매도 시간인지 확인"""
        now = datetime.now()
        target_time = dt_time(8, 0)  # 오전 8시
        current_time = now.time()
        
        # 8:00~8:05 사이인지 확인 (5분 여유)
        return (current_time >= target_time and 
                current_time <= dt_time(8, 5))

# 전역 스케줄러 인스턴스
_scheduler: Optional[TradingScheduler] = None

def get_trading_scheduler(upbit_api: UpbitAPI = None, risk_manager: RiskManager = None) -> Optional[TradingScheduler]:
    """거래 스케줄러 싱글톤 인스턴스 반환"""
    global _scheduler
    
    if _scheduler is None and upbit_api and risk_manager:
        _scheduler = TradingScheduler(upbit_api, risk_manager)
    
    return _scheduler

def start_trading_scheduler(upbit_api: UpbitAPI, risk_manager: RiskManager) -> TradingScheduler:
    """거래 스케줄러 시작"""
    scheduler = get_trading_scheduler(upbit_api, risk_manager)
    if scheduler:
        scheduler.start()
    return scheduler

def stop_trading_scheduler():
    """거래 스케줄러 중지"""
    if _scheduler:
        _scheduler.stop()
