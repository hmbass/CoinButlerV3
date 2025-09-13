"""
업비트 API 연동을 위한 유틸리티 함수들
"""
import os
import requests
import jwt
import uuid
import hashlib
import time
from urllib.parse import urlencode
import pyupbit
from typing import Optional, Dict, List, Any
import pandas as pd
from datetime import datetime, timedelta
import logging
from functools import wraps
import random

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API 호출 제한 관리
class RateLimiter:
    """API 호출 제한 관리 클래스"""
    
    def __init__(self, calls_per_second: int = 8):  # 업비트 제한: 초당 10회, 안전하게 8회로 설정
        self.calls_per_second = calls_per_second
        self.last_call_time = 0
        self.call_interval = 1.0 / calls_per_second
    
    def wait_if_needed(self):
        """필요 시 대기"""
        current_time = time.time()
        time_since_last_call = current_time - self.last_call_time
        
        if time_since_last_call < self.call_interval:
            wait_time = self.call_interval - time_since_last_call
            time.sleep(wait_time)
        
        self.last_call_time = time.time()

# 전역 레이트 리미터
upbit_rate_limiter = RateLimiter()

def api_retry(max_retries: int = 3, delay_base: float = 1.0):
    """API 호출 재시도 데코레이터"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    # 레이트 리미터 적용
                    upbit_rate_limiter.wait_if_needed()
                    return func(*args, **kwargs)
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 429:  # Too Many Requests
                        if attempt < max_retries - 1:
                            delay = delay_base * (2 ** attempt) + random.uniform(0, 1)  # Exponential backoff with jitter
                            logger.warning(f"API 제한 도달, {delay:.2f}초 후 재시도 ({attempt + 1}/{max_retries})")
                            time.sleep(delay)
                            continue
                    raise e
                except Exception as e:
                    if attempt < max_retries - 1:
                        delay = delay_base * (2 ** attempt)
                        logger.error(f"API 호출 실패, {delay:.2f}초 후 재시도 ({attempt + 1}/{max_retries}): {e}")
                        time.sleep(delay)
                        continue
                    raise e
            return None
        return wrapper
    return decorator

class UpbitAPI:
    """업비트 API 래퍼 클래스"""
    
    def __init__(self, access_key: str, secret_key: str):
        self.access_key = access_key
        self.secret_key = secret_key
        self.server_url = "https://api.upbit.com"
        
    def _get_headers(self, query_string: str = "") -> Dict[str, str]:
        """JWT 토큰이 포함된 헤더 생성"""
        payload = {
            'access_key': self.access_key,
            'nonce': str(uuid.uuid4()),
        }
        
        if query_string:
            query_hash = hashlib.sha512(query_string.encode()).hexdigest()
            payload['query_hash'] = query_hash
            payload['query_hash_alg'] = 'SHA512'
        
        jwt_token = jwt.encode(payload, self.secret_key)
        return {
            'Authorization': f'Bearer {jwt_token}',
            'Accept': 'application/json',
        }
    
    def get_accounts(self) -> List[Dict[str, Any]]:
        """계정 정보(잔고) 조회"""
        try:
            headers = self._get_headers()
            response = requests.get(f"{self.server_url}/v1/accounts", headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"계정 정보 조회 실패: {e}")
            return []
    
    def get_krw_balance(self) -> float:
        """원화 잔고 조회"""
        accounts = self.get_accounts()
        for account in accounts:
            if account.get('currency') == 'KRW':
                return float(account.get('balance', 0))
        return 0.0
    
    def get_coin_balance(self, currency: str) -> float:
        """특정 코인 잔고 조회"""
        accounts = self.get_accounts()
        for account in accounts:
            if account.get('currency') == currency:
                return float(account.get('balance', 0))
        return 0.0
    
    @api_retry(max_retries=3, delay_base=2.0)
    def get_current_price(self, market: str) -> Optional[float]:
        """현재가 조회"""
        response = requests.get(f"{self.server_url}/v1/ticker", 
                              params={'markets': market})
        response.raise_for_status()
        data = response.json()
        return float(data[0].get('trade_price', 0)) if data else None
    
    @api_retry(max_retries=3, delay_base=2.0)
    def get_candles(self, market: str, minutes: int = 5, count: int = 200) -> List[Dict[str, Any]]:
        """분봉 데이터 조회"""
        response = requests.get(f"{self.server_url}/v1/candles/minutes/{minutes}",
                              params={'market': market, 'count': count})
        response.raise_for_status()
        return response.json()
    
    def place_buy_order(self, market: str, price: float) -> Optional[Dict[str, Any]]:
        """시장가 매수 주문"""
        try:
            query = {
                'market': market,
                'side': 'bid',
                'price': str(price),
                'ord_type': 'price'
            }
            
            query_string = urlencode(query).encode()
            headers = self._get_headers(query_string.decode())
            
            response = requests.post(f"{self.server_url}/v1/orders",
                                   json=query, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            logger.info(f"매수 주문 완료: {market}, 금액: {price}원")
            return result
            
        except Exception as e:
            logger.error(f"매수 주문 실패 ({market}): {e}")
            return None
    
    def place_sell_order(self, market: str, volume: float) -> Optional[Dict[str, Any]]:
        """시장가 매도 주문"""
        try:
            query = {
                'market': market,
                'side': 'ask',
                'volume': str(volume),
                'ord_type': 'market'
            }
            
            query_string = urlencode(query).encode()
            headers = self._get_headers(query_string.decode())
            
            response = requests.post(f"{self.server_url}/v1/orders",
                                   json=query, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            logger.info(f"매도 주문 완료: {market}, 수량: {volume}")
            return result
            
        except Exception as e:
            logger.error(f"매도 주문 실패 ({market}): {e}")
            return None
    
    def get_order_info(self, uuid: str) -> Optional[Dict[str, Any]]:
        """주문 정보 조회"""
        try:
            query = {'uuid': uuid}
            query_string = urlencode(query)
            headers = self._get_headers(query_string)
            
            response = requests.get(f"{self.server_url}/v1/order?{query_string}",
                                  headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"주문 정보 조회 실패: {e}")
            return None
    
    def get_orders(self, market: str = None, state: str = 'wait') -> List[Dict[str, Any]]:
        """주문 목록 조회"""
        try:
            query = {'state': state}
            if market:
                query['market'] = market
                
            query_string = urlencode(query)
            headers = self._get_headers(query_string)
            
            response = requests.get(f"{self.server_url}/v1/orders?{query_string}",
                                  headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"주문 목록 조회 실패: {e}")
            return []

class MarketAnalyzer:
    """시장 분석 유틸리티"""
    
    def __init__(self, api: UpbitAPI):
        self.api = api
    
    def detect_volume_spike(self, market: str, threshold: float = 2.0) -> bool:
        """거래량 급등 감지"""
        try:
            candles = self.api.get_candles(market, minutes=5, count=10)
            if not candles or len(candles) < 5:
                return False
            
            # 최근 5분봉의 거래량
            recent_volume = float(candles[0]['candle_acc_trade_volume'])
            
            # 이전 4개 봉의 평균 거래량
            prev_volumes = [float(candle['candle_acc_trade_volume']) for candle in candles[1:5]]
            avg_volume = sum(prev_volumes) / len(prev_volumes) if prev_volumes else 0
            
            if avg_volume == 0:
                return False
            
            volume_ratio = recent_volume / avg_volume
            
            logger.info(f"{market} 거래량 비율: {volume_ratio:.2f}")
            
            return volume_ratio >= threshold
            
        except Exception as e:
            logger.error(f"거래량 급등 감지 실패 ({market}): {e}")
            return False
    
    @api_retry(max_retries=3, delay_base=2.0)
    def get_price_change(self, market: str) -> Optional[float]:
        """가격 변동률 조회"""
        response = requests.get(f"{self.api.server_url}/v1/ticker",
                              params={'markets': market})
        response.raise_for_status()
        data = response.json()
        
        if data:
            return float(data[0].get('signed_change_rate', 0))
        return None
    
    @api_retry(max_retries=3, delay_base=2.0)
    def get_tradeable_markets(self) -> List[str]:
        """거래 가능한 KRW 마켓 목록 조회"""
        response = requests.get(f"{self.api.server_url}/v1/market/all")
        response.raise_for_status()
        markets = response.json()
        
        # KRW 마켓만 필터링하고 상위 거래량 기준으로 정렬
        krw_markets = [market['market'] for market in markets 
                      if market['market'].startswith('KRW-')]
        
        return krw_markets[:50]  # 상위 50개만 반환
    
    @api_retry(max_retries=3, delay_base=2.0)
    def get_daily_trade_volume_ranking(self, limit: int = 10) -> List[Dict[str, Any]]:
        """전일자 기준 거래대금 상위 종목 조회"""
        try:
            # 모든 KRW 마켓 조회
            markets_response = requests.get(f"{self.api.server_url}/v1/market/all")
            markets_response.raise_for_status()
            all_markets = markets_response.json()
            
            # KRW 마켓만 필터링
            krw_markets = [market['market'] for market in all_markets 
                          if market['market'].startswith('KRW-')]
            
            # KRW 마켓들의 티커 정보 조회 (최대 100개씩)
            markets_string = ','.join(krw_markets[:100])  # API 제한 고려
            response = requests.get(f"{self.api.server_url}/v1/ticker", 
                                  params={'markets': markets_string})
            response.raise_for_status()
            tickers = response.json()
            
            # KRW 마켓만 필터링하고 거래대금 기준으로 정렬
            krw_tickers = [
                {
                    'market': ticker['market'],
                    'trade_volume': float(ticker.get('acc_trade_volume_24h', 0)),
                    'trade_price': float(ticker.get('acc_trade_price_24h', 0)),  # 거래대금
                    'current_price': float(ticker.get('trade_price', 0)),
                    'change_rate': float(ticker.get('signed_change_rate', 0)),
                    'volume_power': float(ticker.get('acc_trade_volume_24h', 0)) / max(float(ticker.get('prev_closing_price', 1)), 1)
                }
                for ticker in tickers
                if ticker['market'].startswith('KRW-') and 
                   float(ticker.get('acc_trade_price_24h', 0)) > 0  # 거래대금이 0 이상인 것만
            ]
            
            # 거래대금 기준으로 내림차순 정렬
            krw_tickers.sort(key=lambda x: x['trade_price'], reverse=True)
            
            # 상위 N개 반환
            top_markets = krw_tickers[:limit]
            
            logger.info(f"📊 거래대금 상위 {limit}개 종목 조회 완료")
            for i, market in enumerate(top_markets, 1):
                logger.info(f"  {i}위. {market['market']}: {market['trade_price']/100000000:.1f}억원")
            
            return top_markets
            
        except Exception as e:
            logger.error(f"거래대금 랭킹 조회 실패: {e}")
            # 오류 시 기본 상위 종목들 반환
            return self._get_fallback_top_markets(limit)
    
    def _get_fallback_top_markets(self, limit: int) -> List[Dict[str, Any]]:
        """API 오류 시 기본 상위 거래대금 종목들 반환"""
        fallback_markets = [
            'KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-ADA', 'KRW-DOGE',
            'KRW-AVAX', 'KRW-DOT', 'KRW-MATIC', 'KRW-SOL', 'KRW-SHIB'
        ]
        
        result = []
        for market in fallback_markets[:limit]:
            try:
                # 현재가 조회
                current_price = self.get_current_price(market)
                if current_price:
                    result.append({
                        'market': market,
                        'trade_volume': 0,  # 알 수 없음
                        'trade_price': 0,   # 알 수 없음
                        'current_price': current_price,
                        'change_rate': 0,   # 알 수 없음
                        'volume_power': 0   # 알 수 없음
                    })
            except Exception:
                continue
        
        logger.warning(f"⚠️ API 오류로 인해 기본 {len(result)}개 종목 사용")
        return result

def get_upbit_api() -> UpbitAPI:
    """환경 변수에서 업비트 API 인스턴스 생성"""
    access_key = os.getenv('UPBIT_ACCESS_KEY')
    secret_key = os.getenv('UPBIT_SECRET_KEY')
    
    if not access_key or not secret_key:
        raise ValueError("업비트 API 키가 설정되지 않았습니다. .env 파일을 확인해주세요.")
    
    return UpbitAPI(access_key, secret_key)
