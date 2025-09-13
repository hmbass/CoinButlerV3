"""
코인 자동매매 봇의 핵심 로직
"""
import os
import time
import logging
import json
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import google.generativeai as genai
from dotenv import load_dotenv

from trade_utils import UpbitAPI, MarketAnalyzer, get_upbit_api
from risk_manager import RiskManager, get_risk_manager
from market_data_collector import get_market_data_collector
from ai_performance_tracker import get_ai_performance_tracker, AIRecommendation
from config_manager import get_config_manager
from scheduler import start_trading_scheduler, stop_trading_scheduler, get_trading_scheduler
from notifier import (
    init_notifier, notify_buy, notify_sell
)

# 환경변수 로드
load_dotenv()

# 통합 로깅 설정 (멀티프로세싱 대응)
def setup_integrated_logging():
    """멀티프로세싱 환경에서 통합 로깅 설정"""
    # 기존 핸들러 제거
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 통합 로그 파일에 기록
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 파일 핸들러 (main.log와 통합)
    file_handler = logging.FileHandler('coinbutler_main.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # 루트 로거에 핸들러 추가
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(logging.INFO)
    
    return logging.getLogger(__name__)

# 통합 로깅 초기화
logger = setup_integrated_logging()

# 로그 레벨을 환경변수로 설정 가능 (기본: WARNING)
log_level = os.getenv('LOG_LEVEL', 'WARNING').upper()
logger.setLevel(getattr(logging, log_level))

class AIAnalyzer:
    """Google Gemini를 이용한 종목 분석기"""
    
    def __init__(self, api_key: str):
        if api_key:
            try:
                genai.configure(api_key=api_key)
                # 최신 모델명으로 변경: gemini-pro → gemini-1.5-flash
                self.model = genai.GenerativeModel('gemini-1.5-flash')
                self.enabled = True
                logger.info("Gemini AI 모델(gemini-1.5-flash)이 성공적으로 초기화되었습니다.")
            except Exception as e:
                logger.error(f"Gemini AI 초기화 실패: {e}")
                # 대체 모델 시도
                try:
                    self.model = genai.GenerativeModel('gemini-1.5-pro')
                    self.enabled = True
                    logger.info("대체 모델(gemini-1.5-pro)로 초기화 완료")
                except:
                    logger.error("모든 Gemini 모델 초기화 실패")
                    self.enabled = False
        else:
            self.enabled = False
        
    def analyze_market_condition(self, market_data: List[Dict]) -> Dict[str, any]:
        """시장 상황을 분석하여 매수할 종목 추천 (고도화된 분석)"""
        if not self.enabled:
            logger.info("Gemini API 키가 없어서 AI 분석을 건너뜁니다.")
            return {
                "recommended_coin": None,
                "confidence": 0,
                "reason": "AI 분석 비활성화",
                "risk_level": "MEDIUM"
            }
        
        try:
            # 시장 전체 상황 수집
            market_context = self._get_market_context()
            
            # 종목별 상세 분석 데이터 준비
            detailed_analysis = []
            for data in market_data[:3]:  # 상위 3개 분석
                analysis = self._get_detailed_coin_analysis(data)
                detailed_analysis.append(analysis)
            
            # 고도화된 프롬프트 생성
            prompt = self._create_advanced_prompt(market_context, detailed_analysis)
            
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()
            
            # JSON 부분만 추출 (```json 태그 제거)
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.rfind("```")
                response_text = response_text[json_start:json_end].strip()
            
            # JSON 파싱
            result = json.loads(response_text)
            
            # AI 응답 디버깅 로그
            recommended_coin = result.get('recommended_coin', 'N/A')
            logger.debug(f"🤖 AI 응답 recommended_coin: '{recommended_coin}' (원본 응답에서 추출)")
            
            # 신뢰도가 낮으면 fallback 모델 사용 (동적 임계값 적용)
            confidence_threshold = 7  # 기본값, 실제로는 설정에서 가져와야 함
            if hasattr(self, 'parent_bot') and self.parent_bot:
                current_settings = self.parent_bot.get_current_settings()
                confidence_threshold = current_settings.get('ai_confidence_threshold', 7)
            
            if result.get('confidence', 0) < confidence_threshold:
                logger.warning(f"낮은 신뢰도({result.get('confidence')}) - fallback 모델 시도 (임계값: {confidence_threshold})")
                fallback_result = self._analyze_with_fallback_model(market_context, detailed_analysis)
                if fallback_result.get('confidence', 0) > result.get('confidence', 0):
                    result = fallback_result
            
            # AI 추천 저장 (성과 추적용)
            self._save_ai_recommendation(result, market_context, detailed_analysis)
            
            logger.info(f"AI 분석 완료: {result.get('recommended_coin')} (신뢰도: {result.get('confidence')})")
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"AI 응답 JSON 파싱 오류: {e}")
            logger.debug(f"응답 내용: {response_text}")
            return self._get_fallback_recommendation(market_data)
        except Exception as e:
            logger.error(f"AI 분석 오류: {e}")
            return self._get_fallback_recommendation(market_data)
    
    def analyze_profit_potential(self, market_data: List[Dict]) -> Dict:
        """고도화된 수익률 잠재력 분석 (다양한 데이터 활용)"""
        try:
            if not market_data:
                return self._get_profit_fallback_analysis([])
            
            # 1단계: 시장 상황 분석
            market_context = self._get_market_context()
            
            # 2단계: 각 종목별 고도화된 분석
            detailed_analysis = []
            for data in market_data:
                analysis = self._get_advanced_coin_analysis(data)
                detailed_analysis.append(analysis)
            
            # 3단계: 시장 상관관계 및 섹터 분석
            sector_analysis = self._analyze_sector_correlation(detailed_analysis)
            
            # 4단계: 고도화된 예측 프롬프트 생성
            prompt = self._create_advanced_prediction_prompt(market_context, detailed_analysis, sector_analysis)
            
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()
            
            # JSON 부분만 추출
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            
            ai_result = json.loads(response_text)
            
            # AI 추천 기록 (기존 AIRecommendation 구조에 맞게 수정)
            recommendation = AIRecommendation(
                timestamp=datetime.now().isoformat(),
                market=detailed_analysis[0].get('market', '') if detailed_analysis else '',
                recommended_coin=ai_result.get('recommended_coin', ''),
                confidence=ai_result.get('confidence', 0),
                reason=ai_result.get('reason', ''),
                target_return=ai_result.get('expected_profit', 0),  # expected_profit → target_return
                risk_level=ai_result.get('risk_level', 'MEDIUM'),
                entry_strategy='advanced_prediction',  # analysis_type → entry_strategy
                holding_period=ai_result.get('investment_horizon', '6-24시간'),  # investment_horizon → holding_period
                stop_loss=-5.0,  # 기본 손절률
                
                # 시장 컨텍스트 (기본값 제공)
                btc_price=0.0,  # 나중에 실제 데이터로 채움
                fear_greed_index=50,
                btc_dominance=50.0,
                market_sentiment='NEUTRAL',
                
                # 기술적 지표
                rsi=detailed_analysis[0].get('rsi_14', 50) if detailed_analysis else 50,
                macd_trend=detailed_analysis[0].get('macd_trend', 'NEUTRAL') if detailed_analysis else 'NEUTRAL',
                volume_ratio=detailed_analysis[0].get('trade_amount', 0) / 1000 if detailed_analysis else 0,
                price_change=detailed_analysis[0].get('price_change', 0) if detailed_analysis else 0
            )
            
            # 성과 추적 시스템에 저장
            tracker = get_ai_performance_tracker()
            rec_id = tracker.save_recommendation(recommendation)
            
            # 추천 ID를 결과에 추가
            ai_result['recommendation_id'] = rec_id
            ai_result['prediction_factors'] = ai_result.get('prediction_factors', [])
            ai_result['risk_factors'] = ai_result.get('risk_factors', [])
            
            return ai_result
            
        except Exception as e:
            logger.error(f"AI 수익률 분석 오류: {e}")
            # Fallback 분석
            return self._get_profit_fallback_analysis(market_data)
    
    def _create_advanced_prediction_prompt(self, market_context: Dict, detailed_analysis: List[Dict], sector_analysis: Dict) -> str:
        """고도화된 예측 프롬프트 생성 (다양한 데이터 활용)"""
        # 시장 상황 요약
        market_summary = f"""
🌍 전체 시장 상황:
- BTC 현재가: {market_context['btc_price']:,.0f}원 (RSI: {market_context['btc_rsi']:.1f})
- ETH 현재가: {market_context['eth_price']:,.0f}원
- 시장 변동성: {market_context['market_volatility']:.1f}% ({market_context['market_sentiment']})
- Fear & Greed Index: {market_context.get('fear_greed_index', 'N/A')}
- BTC 도미넌스: {market_context.get('btc_dominance', 'N/A')}%
"""
        
        # 섹터 분석 정보
        sector_summary = "📊 섹터 분석:\n"
        if sector_analysis.get('strongest_sector'):
            sector_summary += f"- 최강 섹터: {sector_analysis['strongest_sector']}\n"
        
        # 종목별 고도화된 분석
        coin_analysis = []
        for analysis in detailed_analysis:
            coin_text = f"""
🔍 {analysis['market']} 고도화 분석:
💰 기본 정보:
• 현재가: {analysis['current_price']:,.0f}원 ({analysis['price_change']:+.2f}%)
• 거래대금: {analysis.get('trade_amount', 0):,.0f}만원 (순위: {analysis.get('trade_amount_rank', '?')}위)

📈 기술적 지표:
• RSI: 7일({analysis.get('rsi_7', 50):.1f}) | 14일({analysis.get('rsi_14', 50):.1f}) | 21일({analysis.get('rsi_21', 50):.1f})
• MACD: {analysis.get('macd_trend', 'NEUTRAL')} ({analysis.get('macd_signal_strength', 'WEAK')})
• 스토캐스틱 RSI: K({analysis.get('stoch_rsi_k', 50):.1f}) D({analysis.get('stoch_rsi_d', 50):.1f}) → {analysis.get('stoch_rsi_signal', 'HOLD')}
• 이동평균 정렬: {analysis.get('ma_alignment', 'MIXED')}

🚀 모멘텀 분석:
• 5분: {analysis.get('momentum_5min', 0):+.2f}% | 15분: {analysis.get('momentum_15min', 0):+.2f}% | 30분: {analysis.get('momentum_30min', 0):+.2f}%
• 1시간: {analysis.get('momentum_1h', 0):+.2f}% | 6시간: {analysis.get('momentum_6h', 0):+.2f}% | 12시간: {analysis.get('momentum_12h', 0):+.2f}%
• 모멘텀 점수: {analysis.get('momentum_score', 50):.1f}/100

📊 거래량/패턴 분석:
• 거래량 비율: {analysis.get('volume_ratio', 1):.2f}배 | 추세 점수: {analysis.get('volume_trend', 50):.1f}/100
• 거래대금 비율: {analysis.get('trade_amount_ratio', 1):.2f}배 | 대형 거래: {analysis.get('large_trade_count', 0)}건
• 연속 상승: {analysis.get('consecutive_up', 0)}번 | 연속 하락: {analysis.get('consecutive_down', 0)}번
• 가격 위치: {analysis.get('price_position', 0.5)*100:.1f}% (지지~저항)

⚡ 시장 강도:
• 가격-거래량 상관도: {analysis.get('price_volume_strength', 50):.1f}/100
• 변동성: {analysis.get('volatility', 0):.2f}% ({analysis.get('volatility_level', 'MEDIUM')})
"""
            coin_analysis.append(coin_text)
        
        coins_text = "\n".join(coin_analysis)
        
        return f"""
당신은 세계 최고의 암호화폐 예측 전문가입니다. 방대한 데이터를 종합하여 **수익률이 가장 높을 것으로 예상되는** 1개 종목을 선택하고, **구체적인 상승 이유와 예측 근거**를 제시하세요.

{market_summary}

{sector_summary}

💎 거래대금 상위 후보 종목들 (고도화 분석):
{coins_text}

🎯 **예측 기준 (우선순위 순):**
1. **💰 거래대금 & 유동성**: 높은 거래대금 = 안정적 수익 실현 가능
2. **🚀 복합 모멘텀**: 다양한 시간대 모멘텀이 일치할수록 강력한 신호
3. **📈 기술적 신호 집중**: RSI, MACD, 스토캐스틱 RSI가 모두 매수 신호
4. **📊 거래량 패턴**: 거래량 증가와 가격 상승이 동반될 때 지속성 높음
5. **⚡ 시장 강도**: 가격-거래량 상관도가 높을수록 건전한 상승
6. **🔍 가격 패턴**: 지지선 돌파, 연속 상승 등 기술적 패턴 확인
7. **🌊 섹터 동조화**: 강한 섹터에 속한 종목일수록 추가 상승 기대

💡 **예측 시나리오 고려사항:**
- **단기 모멘텀 + 중기 추세 + 장기 방향성**이 모두 일치하는 종목
- **기술적 돌파 + 거래량 확인 + 섹터 모멘텀** 동반 시 높은 수익률 기대
- **Fear & Greed 지수와 BTC 도미넌스**를 고려한 알트코인 사이클 분석

⚠️ **위험 요소 체크:**
- RSI 80 이상(과매수), 연속 상승 5번 이상(피로감), 변동성 HIGH(위험)
- 거래량 감소 + 가격 상승 = 약한 상승 (지속성 의문)
- 섹터 전체 하락 시 개별 종목도 영향 받을 가능성

🎯 **응답 형식 (필수):**
{{
  "recommended_coin": "BTC",
  "confidence": 8,
  "expected_profit": 12.5,
  "investment_horizon": "6-24시간",
  "reason": "구체적인 선택 이유 (핵심 3가지)",
  "prediction_factors": [
    "모멘텀 집중: 5분~12시간 모든 구간 상승세",
    "기술적 돌파: RSI 50→65, MACD 골든크로스",  
    "거래량 확인: 거래대금 3.2배 증가로 상승 검증"
  ],
  "risk_factors": [
    "단기 과매수 구간 진입 가능성",
    "전체 시장 조정시 동반 하락 위험"
  ],
  "exit_strategy": "목표가 +15% 또는 손절가 -5%",
  "risk_level": "MEDIUM"
}}

**중요: recommended_coin은 "BTC", "ETH", "FLOW" 등 코인명만 입력. "KRW-" 접두사 제외!**
**반드시 JSON 형식으로만 응답하세요.**
"""
    
    def _get_advanced_coin_analysis(self, data: Dict) -> Dict:
        """고도화된 개별 코인 분석 (더 많은 데이터 활용)"""
        try:
            market = data['market']
            
            # 기본 데이터
            analysis = {
                "market": market,
                "current_price": data['current_price'],
                "trade_amount": data.get('trade_amount', 0),
                "price_change": data['price_change'],
            }
            
            # 캔들 데이터 조회 (더 많은 기간)
            candles_5m = self.parent_bot.upbit_api.get_candles(market, minutes=5, count=100)  # 5분봉 100개
            candles_1h = self.parent_bot.upbit_api.get_candles(market, minutes=60, count=48)  # 1시간봉 48개
            candles_4h = self.parent_bot.upbit_api.get_candles(market, minutes=240, count=24) # 4시간봉 24개
            
            if not candles_5m or len(candles_5m) < 50:
                return self._get_simple_coin_analysis(data)
            
            # 가격 데이터 추출
            prices_5m = [float(candle['trade_price']) for candle in candles_5m]
            volumes_5m = [float(candle['candle_acc_trade_volume']) for candle in candles_5m]
            trade_amounts_5m = [float(candle.get('candle_acc_trade_price', 0)) for candle in candles_5m]
            
            # 1. 고도화된 기술적 분석
            analysis.update(self._calculate_advanced_technical_indicators(prices_5m, volumes_5m))
            
            # 2. 모멘텀 분석
            analysis.update(self._calculate_momentum_indicators(prices_5m, candles_1h, candles_4h))
            
            # 3. 거래량/거래대금 패턴 분석
            analysis.update(self._analyze_volume_patterns(volumes_5m, trade_amounts_5m))
            
            # 4. 가격 패턴 분석
            analysis.update(self._analyze_price_patterns(prices_5m))
            
            # 5. 시장 강도 분석
            analysis.update(self._calculate_market_strength(prices_5m, volumes_5m))
            
            return analysis
            
        except Exception as e:
            logger.error(f"고도화된 코인 분석 오류 ({market}): {e}")
            return self._get_simple_coin_analysis(data)
    
    def _calculate_advanced_technical_indicators(self, prices: List[float], volumes: List[float]) -> Dict:
        """고도화된 기술적 지표 계산"""
        indicators = {}
        
        try:
            # RSI (14, 7, 21 기간)
            indicators['rsi_14'] = self._calculate_rsi(prices, 14)
            indicators['rsi_7'] = self._calculate_rsi(prices, 7) 
            indicators['rsi_21'] = self._calculate_rsi(prices, 21)
            
            # MACD (다양한 설정)
            macd_line, signal_line, histogram = self._calculate_macd(prices, 12, 26, 9)
            indicators['macd_line'] = macd_line
            indicators['macd_signal'] = signal_line
            indicators['macd_histogram'] = histogram
            indicators['macd_trend'] = 'BULLISH' if histogram > 0 else 'BEARISH'
            
            # 스토캐스틱 RSI
            stoch_rsi = self._calculate_stochastic_rsi(prices, 14)
            indicators.update(stoch_rsi)
            
            # 볼린저 밴드 (20, 2) - 함수가 없으므로 간단한 계산으로 대체
            if len(prices) >= 20:
                ma_20 = sum(prices[:20]) / 20
                std_20 = (sum((p - ma_20) ** 2 for p in prices[:20]) / 20) ** 0.5
                indicators['bb_upper'] = ma_20 + (std_20 * 2)
                indicators['bb_lower'] = ma_20 - (std_20 * 2)
                indicators['bb_middle'] = ma_20
                indicators['bb_position'] = 'UPPER' if prices[0] > indicators['bb_upper'] else (
                    'LOWER' if prices[0] < indicators['bb_lower'] else 'MIDDLE'
                )
            else:
                indicators['bb_upper'] = prices[0] * 1.05
                indicators['bb_lower'] = prices[0] * 0.95
                indicators['bb_middle'] = prices[0]
                indicators['bb_position'] = 'MIDDLE'
            
            # 이동평균선 (5, 10, 20, 50)
            indicators['ma_5'] = sum(prices[:5]) / 5 if len(prices) >= 5 else prices[0]
            indicators['ma_10'] = sum(prices[:10]) / 10 if len(prices) >= 10 else prices[0]
            indicators['ma_20'] = sum(prices[:20]) / 20 if len(prices) >= 20 else prices[0]
            indicators['ma_50'] = sum(prices[:50]) / 50 if len(prices) >= 50 else prices[0]
            
            # 이동평균 정렬 상태
            mas = [indicators['ma_5'], indicators['ma_10'], indicators['ma_20'], indicators['ma_50']]
            indicators['ma_alignment'] = 'BULLISH' if mas == sorted(mas, reverse=True) else (
                'BEARISH' if mas == sorted(mas) else 'MIXED'
            )
            
            # Volume RSI
            if len(volumes) >= 14:
                indicators['volume_rsi'] = self._calculate_rsi(volumes, 14)
            
        except Exception as e:
            logger.debug(f"기술적 지표 계산 오류: {e}")
            
        return indicators
    
    def _calculate_momentum_indicators(self, prices_5m: List[float], candles_1h: List, candles_4h: List) -> Dict:
        """모멘텀 지표 계산"""
        momentum = {}
        
        try:
            # 5분봉 모멘텀 (5, 15, 30분 전 대비)
            if len(prices_5m) >= 30:
                momentum['momentum_5min'] = (prices_5m[0] - prices_5m[1]) / prices_5m[1] * 100
                momentum['momentum_15min'] = (prices_5m[0] - prices_5m[3]) / prices_5m[3] * 100  
                momentum['momentum_30min'] = (prices_5m[0] - prices_5m[6]) / prices_5m[6] * 100
            
            # 1시간봉 모멘텀
            if candles_1h and len(candles_1h) >= 12:
                prices_1h = [float(c['trade_price']) for c in candles_1h]
                momentum['momentum_1h'] = (prices_1h[0] - prices_1h[1]) / prices_1h[1] * 100
                momentum['momentum_6h'] = (prices_1h[0] - prices_1h[6]) / prices_1h[6] * 100
                momentum['momentum_12h'] = (prices_1h[0] - prices_1h[12]) / prices_1h[12] * 100
            
            # 4시간봉 모멘텀
            if candles_4h and len(candles_4h) >= 6:
                prices_4h = [float(c['trade_price']) for c in candles_4h]
                momentum['momentum_4h'] = (prices_4h[0] - prices_4h[1]) / prices_4h[1] * 100
                momentum['momentum_24h'] = (prices_4h[0] - prices_4h[6]) / prices_4h[6] * 100
            
            # 모멘텀 종합 점수 (0-100)
            momentum_values = [v for k, v in momentum.items() if k.startswith('momentum_')]
            if momentum_values:
                positive_count = sum(1 for v in momentum_values if v > 0)
                momentum['momentum_score'] = (positive_count / len(momentum_values)) * 100
                
        except Exception as e:
            logger.debug(f"모멘텀 지표 계산 오류: {e}")
            
        return momentum
    
    def _analyze_volume_patterns(self, volumes: List[float], trade_amounts: List[float]) -> Dict:
        """거래량/거래대금 패턴 분석"""
        volume_analysis = {}
        
        try:
            if len(volumes) >= 20:
                # 최근 거래량 vs 평균 거래량
                recent_vol = sum(volumes[:5]) / 5
                avg_vol = sum(volumes[5:20]) / 15
                volume_analysis['volume_ratio'] = recent_vol / avg_vol if avg_vol > 0 else 1
                
                # 거래량 증가 추세
                volume_trend = 0
                for i in range(1, min(10, len(volumes))):
                    if volumes[i-1] > volumes[i]:
                        volume_trend += 1
                volume_analysis['volume_trend'] = volume_trend / 9 * 100  # 0-100점
                
            if len(trade_amounts) >= 20:
                # 거래대금 패턴
                recent_amount = sum(trade_amounts[:5]) / 5
                avg_amount = sum(trade_amounts[5:20]) / 15
                volume_analysis['trade_amount_ratio'] = recent_amount / avg_amount if avg_amount > 0 else 1
                
                # 대형 거래 감지 (평균의 3배 이상)
                large_trades = [ta for ta in trade_amounts[:10] if ta > avg_amount * 3]
                volume_analysis['large_trade_count'] = len(large_trades)
                
        except Exception as e:
            logger.debug(f"거래량 패턴 분석 오류: {e}")
            
        return volume_analysis
    
    def _analyze_price_patterns(self, prices: List[float]) -> Dict:
        """가격 패턴 분석"""
        pattern_analysis = {}
        
        try:
            if len(prices) >= 20:
                # 연속 상승/하락 감지
                consecutive_up = 0
                consecutive_down = 0
                current_streak = 0
                
                for i in range(1, min(20, len(prices))):
                    if prices[i-1] > prices[i]:
                        if current_streak >= 0:
                            current_streak = 1
                        else:
                            current_streak += 1
                        consecutive_up = max(consecutive_up, current_streak)
                    elif prices[i-1] < prices[i]:
                        if current_streak <= 0:
                            current_streak = -1
                        else:
                            current_streak -= 1
                        consecutive_down = max(consecutive_down, abs(current_streak))
                
                pattern_analysis['consecutive_up'] = consecutive_up
                pattern_analysis['consecutive_down'] = consecutive_down
                
                # 지지/저항선 분석
                highs = [max(prices[i:i+5]) for i in range(0, len(prices)-5, 5)]
                lows = [min(prices[i:i+5]) for i in range(0, len(prices)-5, 5)]
                
                if highs and lows:
                    resistance = max(highs)
                    support = min(lows)
                    current_price = prices[0]
                    
                    pattern_analysis['price_position'] = (current_price - support) / (resistance - support) if resistance != support else 0.5
                    pattern_analysis['resistance_distance'] = (resistance - current_price) / current_price * 100
                    pattern_analysis['support_distance'] = (current_price - support) / current_price * 100
                
        except Exception as e:
            logger.debug(f"가격 패턴 분석 오류: {e}")
            
        return pattern_analysis
    
    def _calculate_market_strength(self, prices: List[float], volumes: List[float]) -> Dict:
        """시장 강도 분석"""
        strength = {}
        
        try:
            if len(prices) >= 20 and len(volumes) >= 20:
                # 가격-거래량 상관관계
                price_changes = [(prices[i] - prices[i+1]) / prices[i+1] * 100 for i in range(min(19, len(prices)-1))]
                volume_changes = [(volumes[i] - volumes[i+1]) / volumes[i+1] * 100 for i in range(min(19, len(volumes)-1))]
                
                if len(price_changes) == len(volume_changes):
                    # 상승시 거래량 증가, 하락시 거래량 감소 = 강세
                    positive_correlation = sum(1 for i in range(len(price_changes)) 
                                             if (price_changes[i] > 0 and volume_changes[i] > 0) or 
                                                (price_changes[i] < 0 and volume_changes[i] < 0))
                    
                    strength['price_volume_strength'] = positive_correlation / len(price_changes) * 100
                
                # 변동성 분석
                volatility = np.std(price_changes) if price_changes else 0
                strength['volatility'] = volatility
                strength['volatility_level'] = 'HIGH' if volatility > 5 else ('MEDIUM' if volatility > 2 else 'LOW')
                
        except Exception as e:
            logger.debug(f"시장 강도 분석 오류: {e}")
            
        return strength
    
    def _calculate_stochastic_rsi(self, prices: List[float], period: int = 14) -> Dict:
        """스토캐스틱 RSI 계산"""
        try:
            if len(prices) < period * 2:
                return {'stoch_rsi_k': 50, 'stoch_rsi_d': 50}
                
            # RSI 계산
            rsi_values = []
            for i in range(len(prices) - period + 1):
                rsi = self._calculate_rsi(prices[i:i+period], period)
                rsi_values.append(rsi)
            
            if len(rsi_values) < period:
                return {'stoch_rsi_k': 50, 'stoch_rsi_d': 50}
            
            # 스토캐스틱 RSI 계산
            recent_rsi = rsi_values[:period]
            min_rsi = min(recent_rsi)
            max_rsi = max(recent_rsi)
            
            stoch_rsi_k = ((rsi_values[0] - min_rsi) / (max_rsi - min_rsi) * 100) if max_rsi != min_rsi else 50
            
            # %D 계산 (K의 3일 이동평균)
            if len(rsi_values) >= 3:
                k_values = []
                for i in range(min(3, len(rsi_values))):
                    recent_rsi_period = rsi_values[i:i+period] if i+period <= len(rsi_values) else rsi_values[i:]
                    if len(recent_rsi_period) >= 3:
                        min_rsi_period = min(recent_rsi_period)
                        max_rsi_period = max(recent_rsi_period)
                        k = ((rsi_values[i] - min_rsi_period) / (max_rsi_period - min_rsi_period) * 100) if max_rsi_period != min_rsi_period else 50
                        k_values.append(k)
                
                stoch_rsi_d = sum(k_values) / len(k_values) if k_values else 50
            else:
                stoch_rsi_d = stoch_rsi_k
                
            return {
                'stoch_rsi_k': round(stoch_rsi_k, 2),
                'stoch_rsi_d': round(stoch_rsi_d, 2),
                'stoch_rsi_signal': 'BUY' if stoch_rsi_k < 20 else ('SELL' if stoch_rsi_k > 80 else 'HOLD')
            }
            
        except Exception as e:
            logger.debug(f"스토캐스틱 RSI 계산 오류: {e}")
            return {'stoch_rsi_k': 50, 'stoch_rsi_d': 50, 'stoch_rsi_signal': 'HOLD'}
    
    def _analyze_sector_correlation(self, detailed_analysis: List[Dict]) -> Dict:
        """섹터 상관관계 분석"""
        try:
            sector_info = {}
            
            # 주요 코인별 섹터 분류
            sector_map = {
                'BTC': 'store_of_value',
                'ETH': 'smart_contract',
                'ADA': 'smart_contract', 
                'SOL': 'smart_contract',
                'LINK': 'oracle',
                'UNI': 'defi',
                'AAVE': 'defi',
                'CRO': 'exchange',
                'BNB': 'exchange',
                'MATIC': 'layer2',
                'AVAX': 'layer1',
                'DOT': 'interoperability',
                'ATOM': 'interoperability'
            }
            
            # 섹터별 성과 분석
            sector_performance = {}
            for analysis in detailed_analysis:
                coin_symbol = analysis['market'].replace('KRW-', '')
                sector = sector_map.get(coin_symbol, 'others')
                
                if sector not in sector_performance:
                    sector_performance[sector] = []
                
                sector_performance[sector].append({
                    'price_change': analysis.get('price_change', 0),
                    'momentum_score': analysis.get('momentum_score', 50),
                    'trade_amount': analysis.get('trade_amount', 0)
                })
            
            # 각 섹터별 평균 성과
            for sector, coins in sector_performance.items():
                if coins:
                    avg_price_change = sum(c['price_change'] for c in coins) / len(coins)
                    avg_momentum = sum(c['momentum_score'] for c in coins) / len(coins)
                    total_trade_amount = sum(c['trade_amount'] for c in coins)
                    
                    sector_info[f'{sector}_performance'] = avg_price_change
                    sector_info[f'{sector}_momentum'] = avg_momentum
                    sector_info[f'{sector}_liquidity'] = total_trade_amount
            
            # 가장 강한 섹터 찾기
            if sector_performance:
                best_sector = max(sector_performance.keys(), 
                                key=lambda s: sum(c['price_change'] for c in sector_performance[s]) / len(sector_performance[s]))
                sector_info['strongest_sector'] = best_sector
                
            return sector_info
            
        except Exception as e:
            logger.debug(f"섹터 상관관계 분석 오료: {e}")
            return {}
    
    def analyze_future_profitability(self, market: str, current_price: float, current_profit: float, holding_hours: float) -> Optional[Dict]:
        """보유 종목의 향후 12-24시간 수익성 예측"""
        try:
            logger.info(f"🔮 {market} 향후 수익성 AI 분석 중...")
            
            # 1. 현재 종목 상세 분석
            coin_data = {
                'market': market,
                'current_price': current_price,
                'price_change': current_profit  # 현재 수익률을 가격 변화로 사용
            }
            
            detailed_analysis = self._get_advanced_coin_analysis(coin_data)
            
            # 2. 시장 상황 분석
            market_context = self._get_market_context()
            
            # 3. 향후 수익성 예측 프롬프트 생성
            prompt = self._create_future_profitability_prompt(
                market, current_price, current_profit, holding_hours, 
                detailed_analysis, market_context
            )
            
            # 4. AI 분석 실행
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()
            
            # JSON 추출
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            
            ai_result = json.loads(response_text)
            
            # 결과 검증
            if 'expected_profit' in ai_result and 'confidence' in ai_result:
                logger.info(f"✅ {market} AI 예측: {ai_result.get('expected_profit', 0):+.1f}% (신뢰도: {ai_result.get('confidence', 0)}/10)")
                return ai_result
            else:
                logger.warning(f"❌ {market} AI 분석 결과 불완전")
                return None
                
        except Exception as e:
            logger.error(f"향후 수익성 분석 오류 ({market}): {e}")
            return self._get_fallback_future_analysis(current_profit, holding_hours)
    
    def _create_future_profitability_prompt(self, market: str, current_price: float, current_profit: float, 
                                           holding_hours: float, detailed_analysis: Dict, market_context: Dict) -> str:
        """향후 수익성 예측 프롬프트 생성"""
        
        # 기술적 지표 요약
        tech_summary = f"""
📊 {market} 기술적 현황:
• RSI: 7일({detailed_analysis.get('rsi_7', 50):.1f}) | 14일({detailed_analysis.get('rsi_14', 50):.1f}) | 21일({detailed_analysis.get('rsi_21', 50):.1f})
• MACD: {detailed_analysis.get('macd_trend', 'NEUTRAL')}
• 스토캐스틱 RSI: {detailed_analysis.get('stoch_rsi_signal', 'HOLD')}
• 모멘텀 점수: {detailed_analysis.get('momentum_score', 50):.1f}/100
• 이동평균 정렬: {detailed_analysis.get('ma_alignment', 'MIXED')}
• 거래량 비율: {detailed_analysis.get('volume_ratio', 1):.2f}배
• 변동성: {detailed_analysis.get('volatility_level', 'MEDIUM')}
"""
        
        # 시장 상황 요약
        market_summary = f"""
🌍 시장 상황:
• BTC: {market_context['btc_price']:,.0f}원 (RSI: {market_context['btc_rsi']:.1f})
• 시장 심리: {market_context['market_sentiment']}
• 변동성: {market_context['market_volatility']:.1f}%
"""
        
        return f"""
당신은 세계 최고의 암호화폐 예측 전문가입니다. 현재 보유 중인 종목의 **향후 12-24시간 수익성**을 정확히 예측하세요.

📍 **포지션 정보:**
• 종목: {market}
• 현재가: {current_price:,.0f}원
• 현재 수익률: {current_profit:+.2f}%
• 보유 시간: {holding_hours:.1f}시간

{tech_summary}

{market_summary}

🎯 **분석 요청:**
현재 이 종목을 **계속 보유할지 vs 매도할지** 결정하기 위해 다음을 분석하세요:

1. **향후 12-24시간 가격 방향성**: 기술적 지표와 모멘텀 종합 판단
2. **수익률 예측**: -10% ~ +30% 범위에서 구체적 수치
3. **위험 요소**: 급락 가능성, 과매수/과매도 상태
4. **시장 환경**: BTC 영향도, 전체 시장 흐름과의 상관관계

⚠️ **중요 고려사항:**
- 이미 12시간+ 보유한 무수익 상태
- 현재 수익률이 -2% ~ +2% 범위 (답보 상태)
- **-3% 이하 예상시 = 매도 권장**
- **+5% 이상 예상시 = 보유 권장**

🎯 **응답 형식 (필수):**
{{
  "expected_profit": -2.5,
  "confidence": 8,
  "time_horizon": "12-24시간",
  "recommendation": "SELL",
  "reasoning": "기술적 지표 약화 + BTC 하락 영향으로 추가 하락 예상",
  "key_factors": [
    "RSI 70+ 과매수 구간에서 반전 신호",
    "거래량 감소로 상승 동력 부족",
    "BTC 조정 시 알트코인 동반 하락 리스크"
  ],
  "exit_trigger": "현재가 대비 -3% 하락시",
  "alternative_action": "더 강한 모멘텀 종목으로 리밸런싱 권장"
}}

**중요**: expected_profit이 -3 이하면 매도, +3 이상이면 보유를 권장합니다.
**중요: recommended_coin은 "BTC", "ETH", "FLOW" 등 코인명만 입력. "KRW-" 접두사 제외!**
**반드시 JSON 형식으로만 응답하세요.**
"""
    
    def _get_fallback_future_analysis(self, current_profit: float, holding_hours: float) -> Dict:
        """AI 분석 실패시 폴백 분석"""
        # 12시간 이상 무수익이면 보수적으로 -2% 예상
        if holding_hours >= 12 and -1 <= current_profit <= 1:
            expected_profit = -2.0
            recommendation = "SELL"
        else:
            expected_profit = current_profit * 0.8  # 현재 수익률의 80% 정도 유지 예상
            recommendation = "HOLD"
        
        return {
            'expected_profit': expected_profit,
            'confidence': 5,  # 낮은 신뢰도
            'recommendation': recommendation,
            'reasoning': '알고리즘 기반 보수적 예측',
            'key_factors': ['장기 무수익으로 인한 리스크 증가'],
            'alternative_action': '리밸런싱 고려'
        }
    
    def _get_simple_coin_analysis(self, data: Dict) -> Dict:
        """간단한 코인 분석 (고급 분석 실패시 fallback)"""
        try:
            market = data['market']
            
            # 기본 정보만 포함
            analysis = {
                "market": market,
                "current_price": data['current_price'],
                "trade_amount": data.get('trade_amount', 0),
                "price_change": data['price_change'],
                "rsi_14": 50,  # 기본값
                "macd_trend": "NEUTRAL",
                "momentum_score": 50,
                "volume_ratio": 1.0,
                "ma_alignment": "MIXED",
                "volatility_level": "MEDIUM"
            }
            
            # 간단한 RSI 계산 시도
            try:
                candles = self.parent_bot.upbit_api.get_candles(market, minutes=5, count=20)
                if candles and len(candles) >= 14:
                    prices = [float(candle['trade_price']) for candle in candles]
                    analysis['rsi_14'] = self._calculate_simple_rsi(prices)
            except:
                pass  # 기본값 사용
            
            return analysis
            
        except Exception as e:
            logger.debug(f"간단 분석도 실패 ({data.get('market', 'Unknown')}): {e}")
            return {
                "market": data.get('market', ''),
                "current_price": data.get('current_price', 0),
                "trade_amount": data.get('trade_amount', 0),
                "price_change": data.get('price_change', 0),
                "rsi_14": 50,
                "macd_trend": "NEUTRAL",
                "momentum_score": 50,
                "volume_ratio": 1.0,
                "ma_alignment": "MIXED",
                "volatility_level": "MEDIUM"
            }
    
    def _create_profit_analysis_prompt(self, market_context: Dict, detailed_analysis: List[Dict]) -> str:
        """수익률 중심 고도화된 프롬프트 생성"""
        # 시장 상황 요약
        market_summary = f"""
🌍 전체 시장 상황:
- BTC 현재가: {market_context['btc_price']:,.0f}원
- ETH 현재가: {market_context['eth_price']:,.0f}원  
- BTC RSI: {market_context['btc_rsi']:.1f} ({market_context['market_sentiment']})
- 시장 변동성: {market_context['market_volatility']:.1f}%

📊 글로벌 시장 지표:
- Fear & Greed Index: {market_context.get('fear_greed_index', 'N/A')}
- BTC 도미넌스: {market_context.get('btc_dominance', 'N/A')}%
- 전체 시가총액: {market_context.get('total_market_cap', 'N/A')}

"""
        
        # 종목별 상세 분석 (수익률 중심)
        coin_analysis = []
        for analysis in detailed_analysis:
            coin_text = f"""
📊 {analysis['market']}:
• 현재가: {analysis['current_price']:,.0f}원 ({analysis['price_change']:+.2f}%)
• 💰 거래대금: {analysis.get('trade_amount', 0):,.0f}만원 (순위: {analysis.get('trade_amount_rank', '?')}위)
• 📈 수익률 지표:
  - RSI: {analysis.get('rsi', 50):.1f} → {analysis.get('rsi_signal', 'HOLD')} 신호
  - MACD: {analysis.get('macd_trend', 'NEUTRAL')} ({analysis.get('macd_signal_strength', 'WEAK')})
  - 스토캐스틱: K{analysis.get('stoch_k', 50):.1f}/D{analysis.get('stoch_d', 50):.1f} → {analysis.get('stoch_signal', 'HOLD')}
• 💡 기술적 분석:
  - 이동평균: {analysis.get('ma_trend', 'SIDEWAYS')} 추세
  - 볼린저밴드: {analysis.get('bb_position', 'MIDDLE')} 위치  
  - 변동성: {analysis.get('volatility_level', 'MEDIUM')} 수준
  - 가격위치: {analysis.get('price_position', 0.5)*100:.1f}% (지지선~저항선)
"""
            coin_analysis.append(coin_text)
        
        coins_text = "\n".join(coin_analysis)
        
        return f"""
당신은 암호화폐 수익률 전문 분석가입니다. 거래대금 상위 종목들 중에서 **수익률이 가장 높을 것으로 예상되는** 1개 종목을 선택하세요.

{market_summary}

💰 거래대금 상위 후보 종목들:
{coins_text}

🎯 **수익률 중심 선택 기준 (우선순위 순):**
1. **💰 거래대금**: 높은 거래대금 = 높은 유동성 = 안정적 수익 실현
2. **📈 상승 모멘텀**: RSI, MACD, 스토캐스틱이 모두 상승 신호
3. **🔥 기술적 돌파**: 저항선 돌파, 볼린저밴드 상단 돌파 등
4. **⚡ 시장 동조성**: 전체 시장 흐름과 양의 상관관계  
5. **🎢 변동성**: 적절한 변동성으로 수익 기회 창출

💡 **수익률 예상 가이드:**
- **거래대금 1000만원 이상 + 기술적 신호 강함**: 5-15% 수익 기대
- **거래대금 500-1000만원 + 기술적 신호 보통**: 3-10% 수익 기대  
- **거래대금 500만원 미만**: 위험 대비 수익 낮음

⚠️ **주의사항:**
- 이미 큰 폭 상승한 종목(+20% 이상)은 신중 고려
- RSI 80 이상은 과매수로 조정 위험
- 거래대금이 낮으면 아무리 기술적 신호가 좋아도 수익 실현 어려움

**예상 수익률과 근거를 포함하여** 다음 JSON 형식으로만 응답하세요:
{{
  "recommended_coin": "BTC",
  "confidence": 8,
  "expected_profit": 7.5,
  "reason": "거래대금 1위, RSI 돌파, MACD 골든크로스로 7.5% 수익 예상",
  "risk_level": "LOW",
  "investment_horizon": "3-7일"
}}
"""
    
    def _get_profit_fallback_analysis(self, market_data: List[Dict]) -> Dict:
        """수익률 중심 Fallback 분석"""
        if not market_data:
            return {
                "recommended_coin": None,
                "confidence": 0,
                "expected_profit": 0,
                "reason": "분석 가능한 종목 없음",
                "risk_level": "HIGH"
            }
        
        # 거래대금과 기술적 지표를 고려한 점수 계산
        best_candidate = None
        best_score = -1
        
        for data in market_data:
            # 거래대금 점수 (50% 가중치)
            trade_amount = data.get('trade_amount', 0)
            trade_score = min(trade_amount / 1000, 1.0)  # 1000만원을 만점으로 정규화
            
            # 가격 변동 점수 (25% 가중치) - 적절한 상승
            price_change = data.get('price_change', 0)
            if 0 <= price_change <= 15:  # 0~15% 상승이 최적
                price_score = 1.0 - (abs(price_change - 7.5) / 7.5)  # 7.5%를 최적점으로
            elif -5 <= price_change < 0:  # 약간의 하락은 기회
                price_score = 0.7
            else:
                price_score = 0.3
            
            # 포지션 점수 (25% 가중치) - 순위가 높을수록 좋음
            rank_score = max(0, 1 - (data.get('trade_amount_rank', 6) - 1) / 10)
            
            # 종합 점수
            total_score = (trade_score * 0.5) + (price_score * 0.25) + (rank_score * 0.25)
            
            if total_score > best_score:
                best_score = total_score
                best_candidate = data
        
        if best_candidate:
            # 예상 수익률 계산 (거래대금과 기술적 상황 기반)
            trade_amount = best_candidate.get('trade_amount', 0)
            price_change = best_candidate.get('price_change', 0)
            
            if trade_amount >= 1000:  # 1000만원 이상
                expected_profit = 5 + (best_score * 10)  # 5-15%
            elif trade_amount >= 500:  # 500-1000만원
                expected_profit = 3 + (best_score * 7)   # 3-10%
            else:
                expected_profit = 1 + (best_score * 5)   # 1-6%
            
            return {
                "recommended_coin": best_candidate['market'].replace('KRW-', ''),
                "confidence": max(5, int(best_score * 10)),
                "expected_profit": round(expected_profit, 1),
                "reason": f"거래대금 {trade_amount:,.0f}만원(순위{best_candidate.get('trade_amount_rank', '?')}위), 기술적 점수 {best_score:.2f}점으로 {expected_profit:.1f}% 수익 예상",
                "risk_level": "MEDIUM"
            }
        
        return {
            "recommended_coin": None,
            "confidence": 0,
            "expected_profit": 0,
            "reason": "적절한 수익 기회 없음",
            "risk_level": "HIGH"
        }
    
    def _get_market_context(self) -> Dict:
        """전체 시장 상황 분석 (고도화된 외부 데이터 포함)"""
        try:
            # 외부 시장 데이터 수집기 사용
            market_collector = get_market_data_collector()
            
            # 캐시된 데이터 먼저 확인 (30분간 유효)
            cached_context = market_collector.get_cached_market_context(max_age_minutes=30)
            if cached_context:
                logger.info("캐시된 시장 컨텍스트 사용")
                external_data = cached_context
            else:
                logger.info("새로운 시장 데이터 수집 중...")
                external_data = market_collector.get_comprehensive_market_context()
            
            # Upbit 데이터 수집
            # 기존 CoinButler 인스턴스의 upbit_api 사용
            upbit_api = None
            if hasattr(self, 'parent_bot') and hasattr(self.parent_bot, 'upbit_api'):
                upbit_api = self.parent_bot.upbit_api
            
            if not upbit_api:
                from trade_utils import get_upbit_api
                upbit_api = get_upbit_api()
            
            btc_price = upbit_api.get_current_price("KRW-BTC")
            eth_price = upbit_api.get_current_price("KRW-ETH")
            
            # BTC RSI 계산  
            recent_candles = upbit_api.get_candles("KRW-BTC", minutes=5, count=24)
            if recent_candles and len(recent_candles) >= 10:
                prices = [float(candle['trade_price']) for candle in recent_candles[:10]]
                volatility = (max(prices) - min(prices)) / min(prices) * 100
                rsi = self._calculate_simple_rsi(prices)
            else:
                volatility = 0
                rsi = 50
            
            # 종합 시장 컨텍스트 구성
            market_context = {
                # 기본 가격 정보
                "btc_price": btc_price or 0,
                "eth_price": eth_price or 0,
                "market_volatility": volatility,
                "btc_rsi": rsi,
                
                # 외부 데이터
                "fear_greed_index": external_data.get('fear_greed', {}).get('value', 50),
                "fear_greed_classification": external_data.get('fear_greed', {}).get('classification', 'Neutral'),
                "btc_dominance": external_data.get('btc_dominance', {}).get('dominance', 45.0),
                "dominance_interpretation": external_data.get('btc_dominance', {}).get('interpretation', 'NEUTRAL'),
                
                # 글로벌 시장 데이터
                "total_market_cap": external_data.get('global_market', {}).get('total_market_cap', 0),
                "market_cap_change_24h": external_data.get('global_market', {}).get('market_cap_change_24h', 0),
                "total_volume": external_data.get('global_market', {}).get('total_volume', 0),
                
                # 종합 심리
                "overall_sentiment": external_data.get('overall_sentiment', 'NEUTRAL'),
                "market_sentiment": self._determine_market_sentiment(rsi, external_data),
                
                # 트렌딩 코인
                "trending_coins": [coin.get('symbol', '') for coin in external_data.get('trending_coins', [])[:3]],
                
                "analysis_time": datetime.now().isoformat()
            }
            
            logger.info(f"시장 컨텍스트 수집 완료 - 심리: {market_context['overall_sentiment']}, F&G: {market_context['fear_greed_index']}")
            return market_context
            
        except Exception as e:
            logger.error(f"시장 컨텍스트 수집 오류: {e}")
            return {
                "btc_price": 0,
                "eth_price": 0,
                "market_volatility": 5.0,
                "btc_rsi": 50,
                "fear_greed_index": 50,
                "fear_greed_classification": "Neutral",
                "btc_dominance": 45.0,
                "dominance_interpretation": "NEUTRAL",
                "overall_sentiment": "NEUTRAL",
                "market_sentiment": "NEUTRAL",
                "trending_coins": [],
                "analysis_time": datetime.now().isoformat()
            }
    
    def _calculate_simple_rsi(self, prices: List[float]) -> float:
        """간단한 RSI 계산"""
        if len(prices) < 2:
            return 50
            
        gains = []
        losses = []
        for i in range(1, len(prices)):
            change = prices[i-1] - prices[i]  # 최신이 앞에 있음
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _determine_market_sentiment(self, btc_rsi: float, external_data: Dict) -> str:
        """종합적인 시장 심리 판단"""
        sentiment_score = 0
        
        # BTC RSI 기여
        if btc_rsi > 70:
            sentiment_score += 30
        elif btc_rsi > 50:
            sentiment_score += 20
        elif btc_rsi > 30:
            sentiment_score += 10
        
        # Fear & Greed Index 기여
        fng = external_data.get('fear_greed', {}).get('value', 50)
        if fng > 75:
            sentiment_score += 25
        elif fng > 50:
            sentiment_score += 15
        elif fng > 25:
            sentiment_score += 5
        
        # 전체 심리 기여
        overall = external_data.get('overall_sentiment', 'NEUTRAL')
        if overall in ['VERY_BULLISH', 'BULLISH']:
            sentiment_score += 20
        elif overall == 'NEUTRAL':
            sentiment_score += 10
        
        # 결과
        if sentiment_score >= 60:
            return "VERY_BULLISH"
        elif sentiment_score >= 40:
            return "BULLISH"
        elif sentiment_score >= 20:
            return "NEUTRAL"
        elif sentiment_score >= 10:
            return "BEARISH"
        else:
            return "VERY_BEARISH"
    
    def _save_ai_recommendation(self, ai_result: Dict, market_context: Dict, detailed_analysis: List[Dict]):
        """AI 추천을 성과 추적 시스템에 저장"""
        try:
            if not ai_result.get('recommended_coin'):
                return
            
            # 추천된 코인의 상세 분석 찾기 (KRW- 중복 방지)
            recommended_coin = ai_result['recommended_coin']
            if recommended_coin.startswith('KRW-'):
                recommended_market = recommended_coin
            else:
                recommended_market = f"KRW-{recommended_coin}"
            coin_analysis = None
            for analysis in detailed_analysis:
                if analysis['market'] == recommended_market:
                    coin_analysis = analysis
                    break
            
            if not coin_analysis:
                logger.warning(f"추천 코인({recommended_market}) 분석 데이터 없음")
                return
            
            # AIRecommendation 객체 생성
            recommendation = AIRecommendation(
                timestamp=datetime.now().isoformat(),
                market=recommended_market,
                recommended_coin=ai_result['recommended_coin'],
                confidence=ai_result.get('confidence', 0),
                reason=ai_result.get('reason', ''),
                risk_level=ai_result.get('risk_level', 'MEDIUM'),
                entry_strategy=ai_result.get('entry_strategy', '즉시매수'),
                target_return=ai_result.get('target_return', 3.0),
                stop_loss=ai_result.get('stop_loss', -2.0),
                holding_period=ai_result.get('holding_period', '단기(1-3일)'),
                
                # 시장 컨텍스트
                btc_price=market_context.get('btc_price', 0),
                fear_greed_index=market_context.get('fear_greed_index', 50),
                btc_dominance=market_context.get('btc_dominance', 45.0),
                market_sentiment=market_context.get('market_sentiment', 'NEUTRAL'),
                
                # 기술적 지표
                rsi=coin_analysis.get('rsi', 50),
                macd_trend=coin_analysis.get('macd_trend', 'NEUTRAL'),
                volume_ratio=coin_analysis.get('volume_ratio', 1.0),
                price_change=coin_analysis.get('price_change', 0)
            )
            
            # 성과 추적 시스템에 저장
            tracker = get_ai_performance_tracker()
            rec_id = tracker.save_recommendation(recommendation)
            
            # 추천 ID를 결과에 추가 (나중에 업데이트용)
            ai_result['recommendation_id'] = rec_id
            
            logger.info(f"AI 추천 저장 완료: {recommended_market} (ID: {rec_id})")
            
        except Exception as e:
            logger.error(f"AI 추천 저장 실패: {e}")
    
    def _get_detailed_coin_analysis(self, data: Dict) -> Dict:
        """개별 코인의 상세 기술적 분석"""
        try:
            # 기존 CoinButler 인스턴스의 upbit_api 사용
            upbit_api = None
            if hasattr(self, 'parent_bot') and hasattr(self.parent_bot, 'upbit_api'):
                upbit_api = self.parent_bot.upbit_api
            
            if not upbit_api:
                from trade_utils import get_upbit_api
                upbit_api = get_upbit_api()
            
            market = data['market']
            
            # 더 많은 캔들 데이터 수집 (5분봉 100개 = 약 8시간)
            candles = upbit_api.get_candles(market, minutes=5, count=100)
            if not candles or len(candles) < 20:
                return self._get_basic_analysis(data)
            
            # 가격 데이터 추출
            prices = [float(candle['trade_price']) for candle in candles]
            volumes = [float(candle['candle_acc_trade_volume']) for candle in candles]
            highs = [float(candle['high_price']) for candle in candles]
            lows = [float(candle['low_price']) for candle in candles]
            
            # 기술적 지표 계산
            analysis = {
                "market": market,
                "current_price": data['current_price'],
                "volume_ratio": data.get('volume_ratio', 1.0),
                "price_change": data['price_change'],
            }
            
            # RSI 계산 (14기간)
            rsi = self._calculate_rsi(prices, 14)
            analysis["rsi"] = rsi
            analysis["rsi_signal"] = "BUY" if rsi < 30 else ("SELL" if rsi > 70 else "HOLD")
            
            # MACD 계산 (12, 26, 9)
            if len(prices) >= 26:
                macd_line, signal_line, histogram = self._calculate_macd(prices, 12, 26, 9)
                analysis["macd_line"] = macd_line
                analysis["macd_signal"] = signal_line
                analysis["macd_histogram"] = histogram
                analysis["macd_trend"] = "BULLISH" if macd_line > signal_line else "BEARISH"
                analysis["macd_signal_strength"] = "STRONG" if abs(histogram) > abs(macd_line) * 0.1 else "WEAK"
            
            # 스토캐스틱 (14, 3, 3)
            if len(highs) >= 14 and len(lows) >= 14:
                k_percent, d_percent = self._calculate_stochastic(highs, lows, prices, 14, 3)
                analysis["stoch_k"] = k_percent
                analysis["stoch_d"] = d_percent
                analysis["stoch_signal"] = "BUY" if k_percent < 20 and d_percent < 20 else ("SELL" if k_percent > 80 and d_percent > 80 else "HOLD")
            
            # 이동평균선 분석 (5, 20, 60)
            ma5 = sum(prices[:5]) / 5
            ma20 = sum(prices[:20]) / 20 if len(prices) >= 20 else ma5
            ma60 = sum(prices[:60]) / 60 if len(prices) >= 60 else ma20
            
            current_price = prices[0]
            analysis["ma5"] = ma5
            analysis["ma20"] = ma20  
            analysis["ma60"] = ma60
            analysis["ma_trend"] = "BULLISH" if current_price > ma5 > ma20 else ("BEARISH" if current_price < ma5 < ma20 else "SIDEWAYS")
            
            # 볼린저 밴드 (20기간)
            if len(prices) >= 20:
                bb_middle = ma20
                std_dev = (sum([(p - bb_middle) ** 2 for p in prices[:20]]) / 20) ** 0.5
                bb_upper = bb_middle + (2 * std_dev)
                bb_lower = bb_middle - (2 * std_dev)
                
                analysis["bb_upper"] = bb_upper
                analysis["bb_lower"] = bb_lower
                analysis["bb_position"] = "UPPER" if current_price > bb_upper else ("LOWER" if current_price < bb_lower else "MIDDLE")
            
            # 거래량 분석
            recent_volume = sum(volumes[:5]) / 5
            avg_volume = sum(volumes) / len(volumes)
            analysis["volume_trend"] = "HIGH" if recent_volume > avg_volume * 1.5 else ("LOW" if recent_volume < avg_volume * 0.5 else "NORMAL")
            
            # 변동성 분석
            price_volatility = (max(prices[:24]) - min(prices[:24])) / min(prices[:24]) * 100 if len(prices) >= 24 else 0
            analysis["volatility"] = price_volatility
            analysis["volatility_level"] = "HIGH" if price_volatility > 10 else ("LOW" if price_volatility < 3 else "MEDIUM")
            
            # 지지/저항선 분석
            recent_highs = sorted(highs[:20], reverse=True)[:3]
            recent_lows = sorted(lows[:20])[:3]
            resistance = sum(recent_highs) / len(recent_highs)
            support = sum(recent_lows) / len(recent_lows)
            
            analysis["resistance"] = resistance
            analysis["support"] = support
            analysis["price_position"] = (current_price - support) / (resistance - support) if resistance > support else 0.5
            
            return analysis
            
        except Exception as e:
            logger.error(f"상세 분석 오류 ({data['market']}): {e}")
            return self._get_basic_analysis(data)
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """RSI 계산"""
        if len(prices) < period + 1:
            return 50.0
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i-1] - prices[i]  # 최신이 앞에 있으므로 순서 주의
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        if len(gains) < period:
            return 50.0
            
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        if avg_loss == 0:
            return 100.0
            
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_macd(self, prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
        """MACD 계산"""
        if len(prices) < slow:
            return 0, 0, 0
        
        # EMA 계산
        def calculate_ema(data, period):
            multiplier = 2 / (period + 1)
            ema = [data[0]]
            for i in range(1, len(data)):
                ema.append((data[i] * multiplier) + (ema[-1] * (1 - multiplier)))
            return ema
        
        ema_fast = calculate_ema(prices[::-1], fast)[::-1]  # 역순으로 계산 후 다시 역순
        ema_slow = calculate_ema(prices[::-1], slow)[::-1]
        
        macd_line = ema_fast[0] - ema_slow[0]
        
        # Signal line 계산을 위한 MACD 히스토리
        macd_history = []
        for i in range(min(len(ema_fast), len(ema_slow), signal + 5)):
            if i < len(ema_fast) and i < len(ema_slow):
                macd_history.append(ema_fast[i] - ema_slow[i])
        
        if len(macd_history) >= signal:
            signal_ema = calculate_ema(macd_history[::-1], signal)[::-1]
            signal_line = signal_ema[0]
        else:
            signal_line = macd_line
        
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    def _calculate_stochastic(self, highs: List[float], lows: List[float], 
                            prices: List[float], k_period: int = 14, d_period: int = 3) -> tuple:
        """스토캐스틱 계산"""
        if len(highs) < k_period or len(lows) < k_period or len(prices) < k_period:
            return 50, 50
        
        # %K 계산
        highest_high = max(highs[:k_period])
        lowest_low = min(lows[:k_period])
        current_price = prices[0]
        
        if highest_high == lowest_low:
            k_percent = 50
        else:
            k_percent = ((current_price - lowest_low) / (highest_high - lowest_low)) * 100
        
        # %D 계산 (단순화된 버전)
        k_values = []
        for i in range(min(d_period, len(prices))):
            if i + k_period <= len(highs):
                period_high = max(highs[i:i+k_period])
                period_low = min(lows[i:i+k_period])
                if period_high != period_low:
                    k_val = ((prices[i] - period_low) / (period_high - period_low)) * 100
                    k_values.append(k_val)
        
        d_percent = sum(k_values) / len(k_values) if k_values else k_percent
        
        return k_percent, d_percent
    
    def _get_basic_analysis(self, data: Dict) -> Dict:
        """기본 분석 정보 반환"""
        return {
            "market": data['market'],
            "current_price": data['current_price'],
            "volume_ratio": data.get('volume_ratio', 1.0),
            "price_change": data['price_change'],
            "rsi": 50,
            "rsi_signal": "HOLD",
            "macd_trend": "NEUTRAL",
            "macd_signal_strength": "WEAK",
            "stoch_k": 50,
            "stoch_d": 50,
            "stoch_signal": "HOLD",
            "ma_trend": "SIDEWAYS",
            "bb_position": "MIDDLE",
            "volume_trend": "NORMAL",
            "volatility_level": "MEDIUM",
            "price_position": 0.5
        }
    
    def _create_advanced_prompt(self, market_context: Dict, detailed_analysis: List[Dict]) -> str:
        """고도화된 프롬프트 생성"""
        # 시장 상황 요약
        market_summary = f"""
🌍 전체 시장 상황:
- BTC 현재가: {market_context['btc_price']:,.0f}원
- ETH 현재가: {market_context['eth_price']:,.0f}원  
- BTC RSI: {market_context['btc_rsi']:.1f} ({market_context['market_sentiment']})
- 시장 변동성: {market_context['market_volatility']:.1f}%

📊 글로벌 시장 지표:
- Fear & Greed Index: {market_context['fear_greed_index']}/100 ({market_context['fear_greed_classification']})
- BTC 도미넌스: {market_context['btc_dominance']:.1f}% ({market_context['dominance_interpretation']})
- 시가총액 24H 변화: {market_context['market_cap_change_24h']:+.2f}%
- 종합 시장 심리: {market_context['overall_sentiment']}
- 트렌딩 코인: {', '.join(market_context['trending_coins']) if market_context['trending_coins'] else 'N/A'}
"""
        
        # 종목별 상세 분석
        coin_analysis = []
        for analysis in detailed_analysis:
            coin_text = f"""
📊 {analysis['market']}:
• 현재가: {analysis['current_price']:,.0f}원 ({analysis['price_change']:+.2f}%)
• 거래량: {analysis['volume_ratio']:.1f}배 급등 ({analysis.get('volume_trend', 'NORMAL')})
• 💰 거래대금: {analysis.get('trade_amount', 0):,.0f}만원 (순위: {analysis.get('trade_amount_rank', '?')}위)
• RSI: {analysis.get('rsi', 50):.1f} → {analysis.get('rsi_signal', 'HOLD')} 신호
• MACD: {analysis.get('macd_trend', 'NEUTRAL')} ({analysis.get('macd_signal_strength', 'WEAK')})
• 스토캐스틱: K{analysis.get('stoch_k', 50):.1f}/D{analysis.get('stoch_d', 50):.1f} → {analysis.get('stoch_signal', 'HOLD')}
• 이동평균: {analysis.get('ma_trend', 'SIDEWAYS')} 추세
• 볼린저밴드: {analysis.get('bb_position', 'MIDDLE')} 위치
• 변동성: {analysis.get('volatility_level', 'MEDIUM')} 수준
• 가격위치: {analysis.get('price_position', 0.5)*100:.1f}% (지지선~저항선)
"""
            coin_analysis.append(coin_text)
        
        coins_text = "\n".join(coin_analysis)
        
        return f"""
당신은 10년 경력의 암호화폐 전문 트레이더입니다. 
다음 종합 분석을 바탕으로 가장 수익성 높은 1개 종목을 선택하여 추천하세요.

{market_summary}

📈 거래량 급등 후보 종목들:
{coins_text}

🎯 **중요한 선택 기준 (우선순위 순):**
1. **💰 거래대금**: 거래대금이 높을수록 유동성이 풍부하고 수익률이 높음 (최우선 고려)
2. **리스크 vs 수익**: 급등 후 추가 상승 가능성이 높고 하락 리스크는 낮은가?
3. **기술적 신호**: RSI, 이동평균, 볼린저밴드가 모두 매수를 지지하는가?
4. **거래량 지속성**: 거래량 증가가 일회성이 아닌 지속적 관심인가?
5. **시장 상관관계**: 전체 시장 흐름과 동조성이 좋은가?
6. **진입 타이밍**: 지금이 가장 좋은 진입점인가?

💡 **거래대금 가중치 가이드:**
- **1000만원 이상**: 매우 높은 유동성, 최우선 고려 대상
- **500-1000만원**: 높은 유동성, 우선 고려
- **100-500만원**: 보통 유동성, 기술적 분석 중시
- **100만원 미만**: 낮은 유동성, 신중 고려

⚠️ **주의사항:**
- 거래대금이 낮으면 아무리 기술적 신호가 좋아도 피하는 것이 좋음
- RSI 70 이상이면 과매수 구간으로 위험도 높음
- 거래대금 1위라면 다소 높은 RSI도 수용 가능

다음 JSON 형식으로만 응답하세요:
{{
  "recommended_coin": "BTC",
  "confidence": 8,
  "reason": "구체적이고 설득력있는 이유 (기술적 근거 포함)",
  "risk_level": "LOW",
  "entry_strategy": "즉시매수 또는 분할매수",
  "target_return": 5.0,
  "stop_loss": -3.0,
  "holding_period": "단기(1-3일) 또는 중기(1주)"
}}

신뢰도(1-10): 매우 확신할 때만 8 이상 사용
위험도: LOW(안전), MEDIUM(보통), HIGH(위험)

JSON만 출력하세요.
        """
    
    def _analyze_with_fallback_model(self, market_context: Dict, detailed_analysis: List[Dict]) -> Dict:
        """Fallback 모델로 재분석"""
        try:
            # 더 보수적인 gemini-1.5-pro 모델 사용
            fallback_model = genai.GenerativeModel('gemini-1.5-pro')
            
            simple_prompt = """
전문 트레이더 관점에서 다음 3개 종목 중 가장 안전하고 수익성 높은 1개를 선택하세요:

"""
            for analysis in detailed_analysis:
                simple_prompt += f"• {analysis['market']}: 거래대금 {analysis.get('trade_amount', 0):,.0f}만원, 가격변동 {analysis['price_change']:+.2f}%, 거래량 {analysis['volume_ratio']:.1f}배, RSI {analysis.get('rsi', 50):.1f}\n"
            
            simple_prompt += """
JSON으로만 응답:
{
  "recommended_coin": "BTC",
  "confidence": 7,
  "reason": "선택 이유",
  "risk_level": "LOW"
}
"""
            
            response = fallback_model.generate_content(simple_prompt)
            response_text = response.text.strip()
            
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            
            result = json.loads(response_text)
            logger.info("Fallback 모델 분석 성공")
            return result
            
        except Exception as e:
            logger.error(f"Fallback 모델 분석 오류: {e}")
            return self._get_fallback_recommendation(detailed_analysis)
    
    def _get_fallback_recommendation(self, market_data: List[Dict]) -> Dict:
        """최종 Fallback 추천"""
        if not market_data:
            return {
                "recommended_coin": None,
                "confidence": 0,
                "reason": "분석할 데이터 없음",
                "risk_level": "HIGH"
            }
        
        # 가장 안전한 선택: 거래량 대비 가격 변동이 적절한 것
        best_candidate = None
        best_score = -1
        
        for data in market_data:
            # 거래대금 기반 점수 계산: 거래대금 + 거래량 증가 + 적절한 가격 변동
            trade_amount = data.get('trade_amount', 0)
            trade_amount_score = min(trade_amount / 1000, 1.0)  # 1000만원을 만점으로 정규화
            
            volume_score = min(data.get('volume_ratio', 1), 5) / 5  # 최대 5배까지만 점수화
            price_score = 1 - (abs(data['price_change']) / 20)  # 20% 변동을 기준으로 점수화
            
            # 거래대금에 50% 가중치 부여 (기존 50%)
            total_score = (trade_amount_score * 0.5) + ((volume_score + price_score) / 2 * 0.5)
            
            if total_score > best_score:
                best_score = total_score
                best_candidate = data
        
        if best_candidate:
            return {
                "recommended_coin": best_candidate['market'].replace('KRW-', ''),
                "confidence": max(3, int(best_score * 10)),  # 최소 3점
                "reason": f"거래대금 {best_candidate.get('trade_amount', 0):,.0f}만원(순위{best_candidate.get('trade_amount_rank', '?')}위), 거래량 {best_candidate.get('volume_ratio', 1):.1f}배 증가",
                "risk_level": "MEDIUM"
            }
        
        return {
            "recommended_coin": None,
            "confidence": 0,
            "reason": "적절한 후보 없음",
            "risk_level": "HIGH"
        }
    
    def analyze_position_amount(self, market_data: Dict, krw_balance: float, 
                              current_positions: int, max_positions: int) -> Dict[str, any]:
        """분할매수 금액 결정을 위한 AI 분석"""
        if not self.enabled:
            return {
                "investment_amount": min(30000, krw_balance * 0.8),
                "reason": "AI 분석 비활성화 - 기본 금액 사용",
                "split_ratio": 1.0
            }
        
        try:
            market = market_data.get('market', '')
            current_price = market_data.get('current_price', 0)
            volume_ratio = market_data.get('volume_ratio', 2.0)
            price_change = market_data.get('price_change', 0)
            
            available_balance = krw_balance
            remaining_slots = max_positions - current_positions
            
            prompt = f"""
암호화폐 분할매수 전문가로서 다음 정보를 바탕으로 최적의 투자 금액을 결정해주세요:

**종목 정보:**
- 종목: {market}
- 현재가: {current_price:,.0f}원
- 거래량 증가: {volume_ratio:.1f}배
- 💰 거래대금: {market_data.get('trade_amount', 0):,.0f}만원 (순위: {market_data.get('trade_amount_rank', '?')}위)
- 가격 변동: {price_change:+.2f}%

**계정 정보:**
- 사용 가능 잔고: {available_balance:,.0f}원
- 현재 보유 포지션: {current_positions}개
- 남은 포지션 슬롯: {remaining_slots}개

**투자 가이드:**
- 거래대금 1000만원 이상: 적극 투자 (30000-100000원)
- 거래대금 500-1000만원: 보통 투자 (30000-70000원)  
- 거래대금 500만원 미만: 보수적 투자 (30000-50000원)

다음 JSON 형식으로만 응답해주세요:
{{
  "investment_amount": 25000,
  "split_ratio": 0.8,
  "reason": "분할매수 결정 이유",
  "risk_assessment": "LOW"
}}

분할매수 기준:
1. 거래량 급등이 클수록 더 큰 금액 투자
2. 잔고의 60-80% 내에서 결정
3. 남은 포지션 슬롯을 고려한 분산 투자
4. 변동성이 높으면 작은 금액으로 시작

JSON만 출력하세요.
            """
            
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()
            
            # JSON 부분 추출
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            
            import json
            result = json.loads(response_text)
            
            # 안전 검증
            investment_amount = min(result.get('investment_amount', 30000), available_balance * 0.8)
            investment_amount = max(investment_amount, 10000)  # 최소 1만원
            
            result['investment_amount'] = investment_amount
            logger.info(f"Gemini 분할매수 분석: {investment_amount:,.0f}원 ({result.get('split_ratio', 1.0):.1f} 비율)")
            
            return result
            
        except Exception as e:
            logger.error(f"분할매수 AI 분석 실패: {e}")
            return {
                "investment_amount": min(30000, krw_balance * 0.7),
                "reason": "AI 분석 실패로 기본 금액 사용",
                "split_ratio": 0.7,
                "risk_assessment": "MEDIUM"
            }
    
    def analyze_position_swap(self, losing_positions: List[Dict], market_opportunities: List[Dict]) -> Dict[str, any]:
        """손절매수 전환 분석 - 마이너스 포지션을 더 나은 종목으로 교체"""
        if not self.enabled:
            return {
                "should_swap": False,
                "reason": "AI 분석 비활성화",
                "sell_market": None,
                "buy_market": None
            }
        
        if not losing_positions or not market_opportunities:
            return {
                "should_swap": False,
                "reason": "손실 포지션이나 매수 기회가 없음",
                "sell_market": None,
                "buy_market": None
            }
        
        try:
            # 손실 포지션 정보 정리
            losing_info = []
            for pos in losing_positions:
                # entry_time이 문자열이면 datetime으로 변환, 이미 datetime이면 그대로 사용
                entry_time = pos['entry_time']
                if isinstance(entry_time, str):
                    entry_dt = datetime.fromisoformat(entry_time)
                else:
                    entry_dt = entry_time
                days_held = (datetime.now() - entry_dt).days
                losing_info.append(
                    f"- {pos['market']}: 손실률 {pos['pnl_rate']:.2f}%, "
                    f"보유 {days_held}일, 손실액 {pos['pnl']:,.0f}원"
                )
            
            # 매수 기회 정리
            opportunity_info = []
            for opp in market_opportunities[:3]:
                opportunity_info.append(
                    f"- {opp['market']}: 거래대금 {opp.get('trade_amount', 0):,.0f}만원, "
                    f"거래량 {opp.get('volume_ratio', 2.0):.1f}배, "
                    f"가격변동 {opp['price_change']:+.2f}%"
                )
            
            prompt = f"""
암호화폐 포지션 최적화 전문가로서 손절 후 재투자 여부를 결정해주세요.

**현재 손실 포지션들:**
{chr(10).join(losing_info)}

**새로운 매수 기회들:**
{chr(10).join(opportunity_info)}

다음 JSON 형식으로만 응답해주세요:
{{
  "should_swap": true,
  "sell_market": "KRW-BTC",
  "buy_market": "KRW-ETH",
  "confidence": 8,
  "reason": "포지션 교체 결정 이유",
  "expected_recovery_days": 3
}}

판단 기준 (우선순위 순):
1. **💰 거래대금**: 새로운 기회의 거래대금이 높을수록 우선 고려 (500만원 이상 적극 권장)
2. 손실 포지션이 1일 이상 보유되고 -5% 이상 손실
3. 새로운 기회의 상승 가능성이 현재 포지션보다 높음
4. 거래량 급등 강도와 기술적 지표 고려
5. 손절 손실보다 새 투자 수익 예상이 클 때만 교체

교체하지 않으면 should_swap: false로 설정하세요.
JSON만 출력하세요.
            """
            
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()
            
            # JSON 부분 추출
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                response_text = response_text[json_start:json_end].strip()
            
            import json
            result = json.loads(response_text)
            
            logger.info(f"Gemini 포지션 교체 분석: {result.get('should_swap', False)} - {result.get('reason', '')}")
            return result
            
        except Exception as e:
            logger.error(f"포지션 교체 AI 분석 실패: {e}")
            return {
                "should_swap": False,
                "reason": "AI 분석 실패",
                "sell_market": None,
                "buy_market": None
            }

class CoinButler:
    """코인 자동매매 봇 메인 클래스"""
    
    def __init__(self):
        # API 인스턴스 초기화
        self.upbit_api = get_upbit_api()
        self.market_analyzer = MarketAnalyzer(self.upbit_api)
        self.risk_manager = get_risk_manager()
        
        # 설정 관리자 초기화
        self.config_manager = get_config_manager()
        
        # AI 분석기 초기화 (Google Gemini)
        gemini_key = os.getenv('GEMINI_API_KEY')
        if gemini_key:
            self.ai_analyzer = AIAnalyzer(gemini_key)
            # AI 분석기에 부모 봇 참조 전달
            self.ai_analyzer.parent_bot = self
        else:
            self.ai_analyzer = None
        
        # 상태 변수
        self.is_running = False
        self.is_paused = False
        self.last_market_scan = datetime.now() - timedelta(minutes=10)
        self.last_balance_check = datetime.now() - timedelta(minutes=30)
        self.last_rebalance_check = datetime.now() - timedelta(minutes=60)  # 리밸런싱 체크 추가
        
        # 텔레그램 알림 초기화 (상태 확인 추가)
        logger.info("📱 텔레그램 알림 시스템 초기화 시작...")
        init_notifier()
        
        # 텔레그램 초기화 상태 확인
        from notifier import _notifier
        if _notifier:
            logger.info("✅ 텔레그램 알림 시스템 초기화 성공 - 알림 전송 가능")
        else:
            logger.error("❌ 텔레그램 알림 시스템 초기화 실패 - 알림 전송 불가")
            logger.warning("💡 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID 설정을 확인하세요")
        
        # 🕰️ 개선사항 2: 스케줄러 초기화 (매일 오전 8시 전량 매도)
        self.trading_scheduler = None
        logger.info("🕰️ 거래 스케줄러 초기화 대기 중... (봇 시작 시 활성화)")
    
    def get_current_settings(self) -> Dict:
        """현재 설정값들을 가져옴 (동적으로 로드)"""
        return {
            'investment_amount': self.config_manager.get('investment_amount', 30000),
            'min_balance_for_buy': self.config_manager.get('min_balance_for_buy', 30000),
            'max_positions': self.config_manager.get('max_positions', 3),
            'profit_rate': self.config_manager.get('profit_rate', 0.03),
            'loss_rate': self.config_manager.get('loss_rate', -0.02),
            'volume_spike_threshold': self.config_manager.get('volume_spike_threshold', 2.0),
            'price_change_threshold': self.config_manager.get('price_change_threshold', 0.05),
            'check_interval': self.config_manager.get('check_interval', 60),
            'market_scan_interval': self.config_manager.get('market_scan_interval', 10),
            'ai_confidence_threshold': self.config_manager.get('ai_confidence_threshold', 8),  # 7→8로 상향
            'daily_loss_limit': self.config_manager.get('daily_loss_limit', -50000)
        }
        
    def start(self):
        """봇 시작"""
        if self.is_running:
            logger.warning("봇이 이미 실행 중입니다.")
            return
        
        self.is_running = True
        self.is_paused = False
        
        logger.warning("🚀 CoinButler 시작!")
        
        # 기존 포지션 복원 시도
        self._restore_existing_positions()
        
        # 초기 잔고 확인
        settings = self.get_current_settings()
        krw_balance = self.upbit_api.get_krw_balance()
        logger.info(f"현재 KRW 잔고: {krw_balance:,.0f}원")
        
        min_balance = settings['min_balance_for_buy']
        if krw_balance < min_balance:
            warning_msg = f"⚠️ 잔고 부족! 현재: {krw_balance:,.0f}원, 필요: {min_balance:,.0f}원"
            logger.warning(warning_msg)
            logger.info("잔고가 부족하지만 봇은 계속 실행됩니다. 매수는 잔고가 충분할 때만 실행됩니다.")
        
        # 🕰️ 스케줄러 시작 (매일 오전 8시 전량 매도)
        try:
            self.trading_scheduler = start_trading_scheduler(self.upbit_api, self.risk_manager)
            logger.info("✅ 거래 스케줄러 시작됨 - 매일 08:00에 전량 매도 실행")
        except Exception as e:
            logger.error(f"❌ 거래 스케줄러 시작 실패: {e}")
            logger.warning("⚠️ 스케줄러 없이 봇을 계속 실행합니다.")
        
        # 메인 루프 시작 (잔고 부족 시에도 실행)
        self._main_loop()
    
    def stop(self):
        """봇 중지"""
        self.is_running = False
        
        # 스케줄러 중지
        try:
            stop_trading_scheduler()
            logger.info("✅ 거래 스케줄러 중지됨")
        except Exception as e:
            logger.error(f"❌ 거래 스케줄러 중지 실패: {e}")
        
        logger.warning("🛑 CoinButler 중지!")
    
    def pause(self):
        """봇 일시정지"""
        self.is_paused = True
        logger.info("⏸️ CoinButler 일시정지!")
    
    def resume(self):
        """봇 재개"""
        self.is_paused = False
        logger.info("▶️ CoinButler 재개!")
    
    def force_sync_positions(self):
        """포지션을 업비트 실제 잔고와 강제 동기화"""
        try:
            logger.info("🔄 포지션 강제 동기화 요청 받음")
            success = self.risk_manager.force_sync_with_upbit(self.upbit_api)
            
            if success:
                # 동기화 후 현재 포지션 상태 로깅
                current_positions = self.risk_manager.get_open_positions()
                logger.info("📊 동기화 완료 - 현재 포지션 상태:")
                
                for market, position in current_positions.items():
                    current_price = self.upbit_api.get_current_price(market)
                    if current_price:
                        pnl_info = self.risk_manager.get_position_pnl(market, current_price)
                        if pnl_info:
                            pnl, pnl_rate = pnl_info
                            logger.info(f"  {market}: 진입가 {position.entry_price:,.0f}원, 현재가 {current_price:,.0f}원, 손익 {pnl:,.0f}원 ({pnl_rate:+.2f}%)")
                
                logger.info("✅ 포지션 강제 동기화 성공!")
                return True
            else:
                logger.error("❌ 포지션 강제 동기화 실패!")
                return False
                
        except Exception as e:
            logger.error(f"포지션 강제 동기화 오류: {e}")
            return False
    
    def _main_loop(self):
        """메인 거래 루프"""
        try:
            while self.is_running:
                # 현재 설정값 가져오기 (실시간으로 변경될 수 있음)
                settings = self.get_current_settings()
                
                if self.is_paused:
                    time.sleep(settings['check_interval'])
                    continue
                
                # 일일 손실 한도 체크
                if self.risk_manager.check_daily_loss_limit(settings['daily_loss_limit']):
                    daily_pnl = self.risk_manager.get_daily_pnl()
                    logger.warning(f"일일 손실 한도 초과! 현재: {daily_pnl:,.0f}원, 한도: {settings['daily_loss_limit']:,.0f}원")
                    self.pause()
                    continue
                
                # 기존 포지션 관리 (매도 조건 체크)
                self._manage_positions(settings)
                
                # 12시간 리밸런싱 체크 (60분마다 체크)
                if datetime.now() - self.last_rebalance_check > timedelta(minutes=60):
                    self._check_rebalancing_opportunities(settings)
                    self.last_rebalance_check = datetime.now()
                
                # 잔고 상태 주기적 체크 (30분마다)
                if datetime.now() - self.last_balance_check > timedelta(minutes=30):
                    self._check_balance_status(settings)
                    self.last_balance_check = datetime.now()
                
                # 새로운 매수 기회 탐색 (설정된 간격마다)
                scan_interval = settings['market_scan_interval']
                if datetime.now() - self.last_market_scan > timedelta(minutes=scan_interval):
                    self._scan_for_opportunities(settings)
                    self.last_market_scan = datetime.now()
                
                time.sleep(settings['check_interval'])
                
        except KeyboardInterrupt:
            logger.info("사용자에 의한 중단")
        except Exception as e:
            logger.error(f"메인 루프 오류: {e}")
        finally:
            self.stop()
    
    def _manage_positions(self, settings: Dict):
        """기존 포지션 관리 (매도 조건 체크 및 포지션 교체 분석)"""
        open_positions = self.risk_manager.get_open_positions()
        losing_positions = []  # 손실 포지션 수집
        
        for market, position in open_positions.items():
            try:
                current_price = self.upbit_api.get_current_price(market)
                if not current_price:
                    continue
                
                # 매도 조건 확인 (동적 설정 사용) - 진단 로그 추가
                profit_threshold = settings['profit_rate']
                loss_threshold = settings['loss_rate'] 
                
                logger.debug(f"🔍 {market} 손익 체크 - 익절기준: {profit_threshold*100:+.1f}%, 손절기준: {loss_threshold*100:+.1f}%")
                
                should_sell, reason = self.risk_manager.should_sell(
                    market, current_price, profit_threshold, loss_threshold
                )
                
                if should_sell:
                    logger.warning(f"🚨 {market} 매도 결정: {reason}")
                    self._execute_sell(market, current_price, reason)
                else:
                    # 현재 손익 로깅 (더 상세하게)
                    pnl_info = self.risk_manager.get_position_pnl(market, current_price)
                    if pnl_info:
                        pnl, pnl_rate = pnl_info
                        
                        # 손절 근처면 경고 로그
                        if pnl_rate <= -1.5:  # -1.5% 이하면 경고
                            logger.warning(f"⚠️ {market} 손절 임박: {pnl:,.0f}원 ({pnl_rate:+.2f}%) - 손절기준: {loss_threshold*100:+.1f}%")
                        else:
                            logger.debug(f"💰 {market} 현재 손익: {pnl:,.0f}원 ({pnl_rate:+.2f}%)")
                        
                        # 손실 포지션 수집 (포지션 교체 분석용)
                        if pnl_rate < -5.0:  # -5% 이상 손실
                            entry_time = position.get('entry_time', datetime.now().isoformat())
                            try:
                                # entry_time이 문자열이면 datetime으로 변환, 이미 datetime이면 그대로 사용
                                if isinstance(entry_time, str):
                                    entry_dt = datetime.fromisoformat(entry_time)
                                else:
                                    entry_dt = entry_time
                                days_held = (datetime.now() - entry_dt).days
                                if days_held >= 1:  # 1일 이상 보유
                                    losing_positions.append({
                                        'market': market,
                                        'entry_price': position['entry_price'],
                                        'current_price': current_price,
                                        'pnl_rate': pnl_rate,
                                        'pnl': pnl,
                                        'entry_time': entry_time,
                                        'days_held': days_held,
                                        'position': position
                                    })
                            except:
                                pass  # 날짜 파싱 실패시 스킵
                        
            except Exception as e:
                logger.error(f"포지션 관리 오류 ({market}): {e}")
        
        # 손실 포지션이 있고 AI가 활성화된 경우 교체 분석 (5분마다만)
        if (losing_positions and 
            self.ai_analyzer and 
            self.ai_analyzer.enabled and 
            hasattr(self, 'last_swap_check') and
            datetime.now() - self.last_swap_check > timedelta(minutes=5)):
            
            self._analyze_position_swap(losing_positions)
            self.last_swap_check = datetime.now()
        elif not hasattr(self, 'last_swap_check'):
            self.last_swap_check = datetime.now()
    
    def _check_rebalancing_opportunities(self, settings: Dict):
        """12시간 리밸런싱 기회 체크"""
        try:
            open_positions = self.risk_manager.get_open_positions()
            if not open_positions:
                return
            
            logger.info("⏰ 12시간 리밸런싱 체크 시작...")
            
            # 12시간 이상 보유한 무수익 포지션 찾기
            stagnant_positions = []
            current_time = datetime.now()
            
            for market, position in open_positions.items():
                # 포지션 보유 시간 계산
                entry_time = position.entry_time  # 이미 datetime 객체
                if isinstance(entry_time, str):
                    entry_time = datetime.fromisoformat(entry_time)
                holding_hours = (current_time - entry_time).total_seconds() / 3600
                
                if holding_hours >= 12:  # 12시간 이상 보유
                    current_price = self.upbit_api.get_current_price(market)
                    profit_rate = (current_price - position.entry_price) / position.entry_price * 100
                    
                    # 수익률이 -2% ~ +2% 사이인 무수익 포지션
                    if -2.0 <= profit_rate <= 2.0:
                        stagnant_positions.append({
                            'market': market,
                            'position': position,
                            'holding_hours': holding_hours,
                            'profit_rate': profit_rate,
                            'current_price': current_price
                        })
                        logger.info(f"🔍 리밸런싱 후보: {market} (보유: {holding_hours:.1f}시간, 수익률: {profit_rate:+.2f}%)")
            
            if not stagnant_positions:
                logger.info("✅ 12시간 리밸런싱 대상 없음")
                return
            
            # AI를 통한 각 포지션의 향후 수익성 예측
            rebalancing_decisions = []
            for pos_info in stagnant_positions:
                decision = self._analyze_rebalancing_candidate(pos_info, settings)
                if decision:
                    rebalancing_decisions.append(decision)
            
            # 리밸런싱 실행
            if rebalancing_decisions:
                logger.info(f"🔄 {len(rebalancing_decisions)}개 포지션 리밸런싱 실행")
                for decision in rebalancing_decisions:
                    self._execute_rebalancing(decision, settings)
            else:
                logger.info("✅ AI 분석 결과: 리밸런싱 불필요")
                
        except Exception as e:
            logger.error(f"리밸런싱 체크 오류: {e}")
    
    def _analyze_rebalancing_candidate(self, pos_info: Dict, settings: Dict) -> Optional[Dict]:
        """AI를 통한 리밸런싱 후보 분석"""
        try:
            market = pos_info['market']
            current_price = pos_info['current_price']
            profit_rate = pos_info['profit_rate']
            holding_hours = pos_info['holding_hours']
            
            logger.info(f"🤖 AI 리밸런싱 분석 중: {market}")
            
            # 현재 종목의 향후 12-24시간 전망 분석
            future_analysis = self.ai_analyzer.analyze_future_profitability(
                market, current_price, profit_rate, holding_hours
            )
            
            if not future_analysis:
                return None
            
            # AI 신뢰도가 낮으면 리밸런싱 하지 않음
            confidence = future_analysis.get('confidence', 0)
            if confidence < settings.get('ai_confidence_threshold', 7):
                logger.info(f"❌ {market} 리밸런싱 포기: AI 신뢰도 부족 ({confidence}/10)")
                return None
            
            expected_profit = future_analysis.get('expected_profit', 0)
            
            # 향후 수익률이 -3% 이하로 예상되면 리밸런싱
            if expected_profit <= -3:
                logger.info(f"📉 {market} 리밸런싱 결정: 예상 수익률 {expected_profit:+.1f}%")
                
                # 새로운 기회 탐색
                new_opportunity = self._find_rebalancing_opportunity(settings)
                
                return {
                    'sell_market': market,
                    'sell_position': pos_info['position'],
                    'sell_reason': f"AI 예측: 향후 {expected_profit:+.1f}% 손실 예상 (보유 {holding_hours:.1f}h)",
                    'buy_market': new_opportunity.get('market') if new_opportunity else None,
                    'buy_analysis': new_opportunity,
                    'ai_analysis': future_analysis
                }
            else:
                logger.info(f"✅ {market} 보유 유지: AI 예상 수익률 {expected_profit:+.1f}%")
                return None
                
        except Exception as e:
            logger.error(f"리밸런싱 후보 분석 오류 ({market}): {e}")
            return None
    
    def _find_rebalancing_opportunity(self, settings: Dict) -> Optional[Dict]:
        """리밸런싱을 위한 새로운 기회 탐색"""
        try:
            # 현재 스캔 로직을 재사용하여 최적의 기회 1개만 찾기
            markets = self.upbit_api.get_tradeable_markets()
            if not markets:
                return None
            
            # 고거래량 코인 우선 탐색 (20개)
            candidates = []
            for market in markets[:30]:  # 상위 30개만 빠른 탐색
                try:
                    current_price = self.upbit_api.get_current_price(market)
                    price_change = self.market_analyzer.get_price_change(market)
                    trade_amount = self._get_trade_amount(market)
                    
                    if (current_price and -50 <= price_change <= 200 and 
                        trade_amount >= settings.get('min_trade_amount', 100)):  # 50→100
                        
                        candidates.append({
                            'market': market,
                            'current_price': current_price,
                            'price_change': price_change,
                            'trade_amount': trade_amount
                        })
                        
                except Exception:
                    continue
            
            if not candidates:
                return None
            
            # 거래대금 순으로 정렬
            candidates.sort(key=lambda x: x['trade_amount'], reverse=True)
            top_candidates = candidates[:5]  # 상위 5개만 AI 분석
            
            # AI 분석으로 최적 기회 선택
            best_analysis = self.ai_analyzer.analyze_profit_potential(top_candidates)
            
            if best_analysis and best_analysis.get('confidence', 0) >= settings.get('ai_confidence_threshold', 7):
                return best_analysis
            
            # AI 분석 실패 시 최고 거래대금 종목 선택
            return top_candidates[0] if top_candidates else None
            
        except Exception as e:
            logger.error(f"리밸런싱 기회 탐색 오류: {e}")
            return None
    
    def _execute_rebalancing(self, decision: Dict, settings: Dict):
        """리밸런싱 실행 (매도 + 매수)"""
        try:
            sell_market = decision['sell_market']
            sell_position = decision['sell_position']
            sell_reason = decision['sell_reason']
            buy_analysis = decision.get('buy_analysis')
            
            logger.info(f"🔄 리밸런싱 실행: {sell_market} → {buy_analysis.get('market', '미정') if buy_analysis else '대기'}")
            
            # 1단계: 기존 포지션 매도
            success = self._execute_sell(sell_market, "REBALANCING", sell_reason)
            
            if success and buy_analysis:
                # 매도 성공 시 새로운 포지션 매수
                time.sleep(1)  # API 호출 간격 
                
                buy_market = buy_analysis.get('market') or buy_analysis.get('recommended_coin', '')
                if buy_market and not buy_market.startswith('KRW-'):
                    buy_market = f"KRW-{buy_market}"
                
                if buy_market:
                    logger.info(f"🛒 리밸런싱 매수: {buy_market}")
                    
                    # 매수 실행
                    buy_success = self._execute_buy(buy_market, buy_analysis, settings)
                    
                    if buy_success:
                        logger.info(f"✅ 리밸런싱 완료: {sell_market} → {buy_market}")
                        
                        # 리밸런싱 알림
                        from notifier import notify_rebalancing
                        notify_rebalancing(
                            sell_market=sell_market,
                            buy_market=buy_market,
                            reason=sell_reason,
                            expected_profit=buy_analysis.get('expected_profit', 0)
                        )
                    else:
                        logger.warning(f"❌ 리밸런싱 매수 실패: {buy_market}")
                else:
                    logger.warning("❌ 리밸런싱 매수 대상 없음")
            else:
                logger.warning(f"❌ 리밸런싱 매도 실패: {sell_market}")
                
        except Exception as e:
            logger.error(f"리밸런싱 실행 오류: {e}")
    
    def _check_balance_status(self, settings: Dict):
        """잔고 상태 체크 및 정보 제공"""
        try:
            krw_balance = self.upbit_api.get_krw_balance()
            min_balance = settings['min_balance_for_buy']
            
            if krw_balance >= min_balance:
                logger.info(f"💰 잔고 상태: 양호 ({krw_balance:,.0f}원 / {min_balance:,.0f}원 필요)")
            else:
                shortage = min_balance - krw_balance
                logger.warning(f"💰 잔고 부족: {krw_balance:,.0f}원 (부족: {shortage:,.0f}원)")
                logger.info(f"💡 매수를 위해 {shortage:,.0f}원을 입금해주세요.")
                
        except Exception as e:
            logger.error(f"잔고 상태 체크 오류: {e}")
    
    def _restore_existing_positions(self):
        """봇 재시작 시 기존 포지션 복원"""
        try:
            logger.info("🔄 기존 포지션 복원 시도 중...")
            
            # 1. 파일에서 포지션 복원 (이미 RiskManager 초기화 시 완료)
            open_positions = self.risk_manager.get_open_positions()
            
            if open_positions:
                logger.info(f"파일에서 {len(open_positions)}개 포지션 복원")
                for market, position in open_positions.items():
                    logger.info(f"- {market}: 진입가 {position.entry_price:,.0f}원, 수량 {position.quantity:.6f}")
            else:
                logger.info("저장된 포지션이 없습니다.")
            
            # 2. Upbit API에서 실제 잔고 확인 및 동기화
            logger.info("Upbit 실제 잔고와 동기화 중...")
            self.risk_manager.restore_positions_from_upbit(self.upbit_api)
            
            # 3. 복원 완료 후 현재 포지션 상태 표시
            final_positions = self.risk_manager.get_open_positions()
            if final_positions:
                logger.info(f"✅ 총 {len(final_positions)}개 포지션 복원 완료:")
                
                total_investment = 0
                total_current_value = 0
                
                for market, position in final_positions.items():
                    current_price = self.upbit_api.get_current_price(market)
                    if current_price:
                        current_value = position.quantity * current_price
                        pnl = current_value - position.investment_amount
                        pnl_rate = (pnl / position.investment_amount) * 100
                        
                        total_investment += position.investment_amount
                        total_current_value += current_value
                        
                        logger.info(f"  {market}: {pnl:,.0f}원 ({pnl_rate:+.2f}%)")
                
                if total_investment > 0:
                    total_pnl = total_current_value - total_investment
                    total_pnl_rate = (total_pnl / total_investment) * 100
                    logger.info(f"📊 전체 미실현 손익: {total_pnl:,.0f}원 ({total_pnl_rate:+.2f}%)")
            else:
                logger.info("복원된 포지션이 없습니다. 새로 거래를 시작합니다.")
                
        except Exception as e:
            logger.error(f"포지션 복원 중 오류: {e}")
            logger.info("포지션 복원에 실패했지만 봇은 계속 실행됩니다.")
    
    def _scan_for_opportunities(self, settings: Dict):
        """새로운 매수 기회 탐색 - 거래대금 TOP10 종목만 대상"""
        # 최대 포지션 수 체크 (동적 설정 사용)
        open_positions_count = len(self.risk_manager.get_open_positions())
        max_positions = settings['max_positions']
        
        if open_positions_count >= max_positions:
            logger.info(f"최대 포지션 수 도달로 인한 매수 스킵 ({open_positions_count}/{max_positions})")
            return
        
        try:
            logger.info("🔍 매수 기회 탐색 중... (거래대금 TOP10 종목 한정)")
            
            # 🎯 개선사항 1: 전일자 기준 거래대금 상위 10개 종목만 조회
            try:
                top10_markets = self.market_analyzer.get_daily_trade_volume_ranking(limit=10)
                if not top10_markets:
                    logger.warning("거래대금 TOP10 종목 조회 실패")
                    return
            except Exception as e:
                logger.error(f"거래대금 TOP10 조회 실패: {e}")
                return
            
            high_volume_candidates = []
            
            logger.info(f"📊 거래대금 TOP10 종목에서 매수 후보 선별 중...")
            
            # TOP10 종목만 스캔 (기존 50개 → 10개로 대폭 축소)
            for i, market_data in enumerate(top10_markets):
                try:
                    market = market_data['market']
                    
                    # 이미 조회된 데이터 사용 (API 호출 최소화)
                    current_price = market_data.get('current_price', 0)
                    trade_amount = market_data.get('trade_price', 0) / 10000  # 원 → 만원 단위
                    price_change = market_data.get('change_rate', 0) * 100  # 소수 → 퍼센트
                    
                    # 이미 TOP10이므로 거래대금 필터링 생략
                    if current_price and price_change is not None:
                        # 극단적 변동만 제외 (-50% ~ +200%)
                        if -50 <= price_change <= 200:
                            high_volume_candidates.append({
                                'market': market,
                                'current_price': current_price,
                                'price_change': price_change,
                                'trade_amount': trade_amount,
                                'trade_amount_rank': i + 1,  # TOP10 순위 바로 적용
                                'volume_ratio': market_data.get('volume_power', 1.0)
                            })
                            
                            logger.debug(f"✅ {market}: 거래대금 {trade_amount:,.0f}만원 ({i+1}위), 변동률 {price_change:+.2f}%")
                                
                except Exception as e:
                    logger.debug(f"TOP10 종목 처리 실패 ({market_data.get('market', 'Unknown')}): {e}")
                    continue
            
            if not high_volume_candidates:
                logger.warning("❌ TOP10 종목 중 매수 조건을 만족하는 종목이 없습니다.")
                return
            
            # 이미 거래대금 순으로 정렬되어 있으므로 추가 정렬 불필요
            logger.info(f"💰 거래대금 TOP10 중 {len(high_volume_candidates)}개 종목이 매수 조건 충족")
            logger.info("📊 매수 후보 종목: " + 
                       ", ".join([f"{c['market'].replace('KRW-', '')}: {c['trade_amount']:,.0f}만원({c['trade_amount_rank']}위)" 
                                 for c in high_volume_candidates[:5]]))
            
            # AI 수익률 분석을 위한 후보들 선택 (최대 5개)
            ai_candidates = high_volume_candidates[:5]
            logger.info(f"🤖 AI 수익률 분석 대상: {len(ai_candidates)}개 종목")
            
            # AI 분석 (수익률 중심) - 거래대금 상위 종목들을 수익률 관점에서 분석
            # 🚨 임시: 단순 선택 모드 옵션 추가
            use_simple_mode = settings.get('use_simple_selection', False)
            
            if use_simple_mode:
                logger.warning("🔄 단순 선택 모드 활성화: AI 분석 우회")
                # 거래대금 + 가격변동률 기반 점수 계산
                for candidate in high_volume_candidates[:5]:
                    trade_score = candidate.get('trade_amount', 0) * 0.7
                    price_score = min(max(candidate.get('price_change', 0), 0), 15) * 20 * 0.3
                    candidate['simple_score'] = trade_score + price_score
                
                high_volume_candidates.sort(key=lambda x: x.get('simple_score', 0), reverse=True)
                best_candidate = high_volume_candidates[0]
                best_candidate['confidence'] = 6  # 중간 신뢰도
                best_candidate['reason'] = f"단순모드: 대금 {best_candidate.get('trade_amount', 0):.0f}만원 + 변동 {best_candidate.get('price_change', 0):+.1f}%"
                
                logger.info(f"📊 단순모드 결과: {best_candidate['market']}")
                self._execute_buy(best_candidate, settings)
                self.last_market_scan = datetime.now()
                return
            
            best_candidate = high_volume_candidates[0]  # 기본값: 거래대금 1위 종목
            
            if self.ai_analyzer and self.ai_analyzer.enabled and len(high_volume_candidates) > 1:
                try:
                    # 수익률 중심 AI 분석 실행
                    ai_result = self.ai_analyzer.analyze_profit_potential(ai_candidates)
                    
                    confidence_threshold = settings['ai_confidence_threshold']
                    if (ai_result.get('recommended_coin') and 
                        ai_result.get('confidence', 0) >= confidence_threshold and 
                        ai_result.get('risk_level') != 'HIGH'):
                        
                        # AI 추천 종목 찾기 (KRW- 중복 방지)
                        recommended_coin = ai_result['recommended_coin']
                        if recommended_coin.startswith('KRW-'):
                            recommended_market = recommended_coin  # 이미 KRW- 포함됨
                        else:
                            recommended_market = f"KRW-{recommended_coin}"  # KRW- 추가
                        
                        logger.debug(f"🔍 AI 추천 처리: '{recommended_coin}' → '{recommended_market}'")
                        
                        for candidate in high_volume_candidates:
                            if candidate['market'] == recommended_market:
                                best_candidate = candidate
                                # AI 추천 ID를 candidate에 추가 (성과 추적용)
                                best_candidate['recommendation_id'] = ai_result.get('recommendation_id')
                                logger.info(f"🎯 AI 추천 종목: {recommended_market} (신뢰도: {ai_result['confidence']}, 예상수익: {ai_result.get('expected_profit', 'N/A')}%)")
                                break
                        else:
                            logger.info(f"AI 추천 종목({recommended_market})이 후보에 없어서 거래대금 1위 선택")
                    else:
                        logger.info(f"AI 분석 결과 신뢰도 부족 또는 고위험 - 거래대금 1위 선택")
                        
                except Exception as e:
                    logger.error(f"AI 수익률 분석 중 오류: {e}")
                    logger.info("AI 분석 실패로 거래대금 1위 선택")
            else:
                if not self.ai_analyzer or not self.ai_analyzer.enabled:
                    logger.info("AI 분석 비활성화 - 거래대금 1위 선택")
                else:
                    logger.info("후보가 1개뿐이어서 AI 분석 건너뜀")
            
            # 매수 실행
            self._execute_buy(best_candidate, settings)
            
        except Exception as e:
            logger.error(f"매수 기회 탐색 오류: {e}")
    
    def _get_trade_amount(self, market: str) -> float:
        """특정 종목의 5분봉 거래대금 조회 (만원 단위)"""
        try:
            candles = self.upbit_api.get_candles(market, minutes=5, count=1)
            if candles and len(candles) > 0:
                # candle_acc_trade_price는 원 단위 거래대금
                trade_amount_krw = float(candles[0].get('candle_acc_trade_price', 0))
                # 만원 단위로 변환
                trade_amount_man = trade_amount_krw / 10000
                return trade_amount_man
            return 0.0
        except Exception as e:
            logger.error(f"거래대금 조회 실패 ({market}): {e}")
            return 0.0
    
    def _execute_buy(self, candidate: Dict, settings: Dict):
        """매수 실행 (분할매수 지원)"""
        market = candidate['market']
        current_price = candidate['current_price']
        
        try:
            # 현재 잔고 확인 (동적 설정 사용)
            krw_balance = self.upbit_api.get_krw_balance()
            min_balance = settings['min_balance_for_buy']
            
            if krw_balance < min_balance:
                logger.warning(f"💰 잔고 부족으로 매수 스킵: {market} (현재: {krw_balance:,.0f}원, 필요: {min_balance:,}원 이상)")
                return
            
            # AI 분할매수 분석
            open_positions = self.risk_manager.get_open_positions()
            current_positions = len(open_positions)
            
            if self.ai_analyzer and self.ai_analyzer.enabled:
                max_positions = settings['max_positions']
                amount_analysis = self.ai_analyzer.analyze_position_amount(
                    candidate, krw_balance, current_positions, max_positions
                )
                investment_amount = amount_analysis['investment_amount']
                logger.info(f"🤖 AI 분할매수 결정: {investment_amount:,.0f}원 - {amount_analysis['reason']}")
            else:
                # AI가 없는 경우 기본 로직
                base_investment = settings['investment_amount']
                investment_amount = min(base_investment, krw_balance * 0.8)
                logger.info(f"💰 기본 매수 금액: {investment_amount:,.0f}원")
            
            # 최종 잔고 체크
            if krw_balance < investment_amount:
                logger.warning(f"💰 잔고 부족으로 매수 스킵: {market} (현재: {krw_balance:,.0f}원, 필요: {investment_amount:,.0f}원)")
                return
            
            # 매수 주문 실행
            order_result = self.upbit_api.place_buy_order(market, investment_amount)
            if not order_result:
                logger.error(f"매수 주문 실패: {market}")
                return
            
            # 주문 완료까지 대기 및 확인 (개선된 로깅)
            logger.info(f"💫 매수 주문 체결 대기 중: {market} (UUID: {order_result['uuid']})")
            time.sleep(3)
            
            order_info = self.upbit_api.get_order_info(order_result['uuid'])
            
            if order_info:
                order_state = order_info.get('state', 'unknown')
                executed_volume = float(order_info.get('executed_volume', 0))
                logger.info(f"📋 주문 상태 확인: {market} → {order_state} (체결량: {executed_volume})")
                
                if order_state == 'done':
                    logger.info(f"✅ 매수 주문 체결 완료: {market}")
                elif order_state == 'wait':
                    logger.warning(f"⏰ 매수 주문 대기 중: {market} - 추가 대기 시도")
                    # 추가 대기 시간
                    time.sleep(5)
                    order_info = self.upbit_api.get_order_info(order_result['uuid'])
                    order_state = order_info.get('state', 'unknown') if order_info else 'unknown'
                    executed_volume = float(order_info.get('executed_volume', 0)) if order_info else 0
                    logger.info(f"🔄 재확인 결과: {market} → {order_state} (체결량: {executed_volume})")
                elif order_state == 'cancel':
                    # cancel 상태라도 부분 체결이 있을 수 있음
                    if executed_volume > 0:
                        logger.warning(f"🔄 {market} 주문 취소되었지만 부분 체결됨: {executed_volume}")
                    else:
                        logger.warning(f"❌ {market} 주문 취소: 체결량 0")
                else:
                    logger.warning(f"❓ 예상치 못한 주문 상태: {market} → {order_state}")
            else:
                logger.error(f"❌ 주문 정보 조회 실패: {market}")
            
            # 체결 여부 판단: 상태가 'done'이거나 executed_volume > 0이면 체결된 것으로 처리
            is_filled = order_info and (order_info.get('state') == 'done' or float(order_info.get('executed_volume', 0)) > 0)
            
            if is_filled:
                # 실제 체결된 수량과 평균가 계산
                executed_volume = float(order_info.get('executed_volume', 0))
                avg_price = float(order_info.get('avg_price', current_price))
                
                if executed_volume > 0:
                    # 포지션 추가 (실제 투자된 금액 사용)
                    actual_investment = executed_volume * avg_price
                    success = self.risk_manager.add_position(
                        market=market,
                        entry_price=avg_price,
                        quantity=executed_volume,
                        investment_amount=actual_investment
                    )
                    
                    if success:
                        # 매수 알림
                        if self.ai_analyzer and self.ai_analyzer.enabled:
                            reason = f"AI 분할매수 {investment_amount:,.0f}원 (거래대금 {candidate.get('trade_amount', 0):,.0f}만원, 거래량 {candidate.get('volume_ratio', 0):.1f}배)"
                        else:
                            reason = f"거래대금 {candidate.get('trade_amount', 0):,.0f}만원, 거래량 {candidate.get('volume_ratio', 0):.1f}배 급등"
                        
                        logger.info(f"📱 매수 텔레그램 알림 전송 시도: {market}")
                        logger.debug(f"📋 알림 파라미터: market={market}, price={avg_price:,.0f}, amount={actual_investment:,.0f}, reason={reason}")
                        
                        try:
                            notify_buy(market, avg_price, actual_investment, reason)
                            logger.info(f"✅ 텔레그램 알림 호출 완료: {market}")
                        except Exception as e:
                            logger.error(f"❌ 텔레그램 알림 호출 실패: {market} - {e}")
                        
                        logger.warning(f"💰 매수 완료: {market}, 가격: {avg_price:,.0f}원, 투자: {actual_investment:,.0f}원")
                        
                        # AI 추천 성과 추적 업데이트
                        self._update_ai_recommendation_execution(candidate, avg_price)
                    else:
                        logger.error(f"포지션 추가 실패: {market}")
                else:
                    logger.error(f"체결 수량 0: {market}")
            else:
                # 상세한 실패 정보 제공
                if order_info:
                    final_state = order_info.get('state', 'unknown')
                    final_executed = float(order_info.get('executed_volume', 0))
                    if final_executed > 0:
                        logger.error(f"🔄 {market} 부분 체결: 상태 {final_state}, 체결량 {final_executed} (포지션 추가 실패)")
                    else:
                        logger.error(f"💥 {market} 매수 주문 체결 실패: 상태 {final_state}, 체결량 0")
                    logger.debug(f"📊 주문 세부정보: {order_info}")
                else:
                    logger.error(f"💥 {market} 매수 주문 정보 조회 불가 (API 응답 없음)")
                
        except Exception as e:
            logger.error(f"매수 실행 오류 ({market}): {e}")
    
    def _update_ai_recommendation_execution(self, candidate: Dict, execution_price: float):
        """AI 추천 매수 실행 업데이트"""
        try:
            recommendation_id = candidate.get('recommendation_id')
            if recommendation_id and recommendation_id > 0:
                tracker = get_ai_performance_tracker()
                success = tracker.update_recommendation_result(
                    recommendation_id, execution_price
                )
                if success:
                    logger.info(f"AI 추천 매수 실행 업데이트 완료: ID {recommendation_id}")
                else:
                    logger.error(f"AI 추천 매수 실행 업데이트 실패: ID {recommendation_id}")
        except Exception as e:
            logger.error(f"AI 추천 매수 실행 업데이트 오류: {e}")
    
    def _update_ai_recommendation_exit(self, market: str, exit_price: float):
        """AI 추천 매도 완료 업데이트"""
        try:
            # 최근 실행된 추천 중에서 해당 마켓 찾기
            tracker = get_ai_performance_tracker()
            recent_recs = tracker.get_recent_recommendations(50)
            
            for rec in recent_recs:
                if (rec['market'] == market and 
                    rec['executed'] and 
                    rec['actual_return'] is None):  # 아직 매도되지 않은 것
                    
                    # 추천 ID 찾기 (DB에서 직접 조회)
                    import sqlite3
                    with sqlite3.connect(tracker.db_path) as conn:
                        cursor = conn.execute("""
                            SELECT id FROM ai_recommendations 
                            WHERE market = ? AND executed = 1 AND exit_price IS NULL
                            ORDER BY timestamp DESC LIMIT 1
                        """, (market,))
                        row = cursor.fetchone()
                        
                        if row:
                            recommendation_id = row[0]
                            success = tracker.update_recommendation_result(
                                recommendation_id, 
                                None,  # execution_price는 이미 있음
                                exit_price, 
                                datetime.now().isoformat()
                            )
                            if success:
                                logger.info(f"AI 추천 매도 완료 업데이트: {market} (ID: {recommendation_id})")
                            break
                            
        except Exception as e:
            logger.error(f"AI 추천 매도 완료 업데이트 오류: {e}")
    
    def _execute_sell(self, market: str, current_price: float, reason: str):
        """매도 실행"""
        try:
            position = self.risk_manager.positions.get(market)
            if not position or position.status != "open":
                logger.warning(f"매도할 포지션 없음: {market}")
                return
            
            # 매도 주문 실행
            order_result = self.upbit_api.place_sell_order(market, position.quantity)
            if not order_result:
                logger.error(f"매도 주문 실패: {market}")
                return
            
            # 주문 완료까지 대기 (개선된 로깅)
            logger.info(f"💫 매도 주문 체결 대기 중: {market} (UUID: {order_result['uuid']})")
            time.sleep(3)
            
            order_info = self.upbit_api.get_order_info(order_result['uuid'])
            
            if order_info:
                order_state = order_info.get('state', 'unknown')
                executed_volume = float(order_info.get('executed_volume', 0))
                logger.info(f"📋 매도 주문 상태 확인: {market} → {order_state} (체결량: {executed_volume})")
                
                if order_state == 'done':
                    logger.info(f"✅ 매도 주문 체결 완료: {market}")
                elif order_state == 'wait':
                    logger.warning(f"⏰ 매도 주문 대기 중: {market} - 추가 대기 시도")
                    # 추가 대기 시간
                    time.sleep(5)
                    order_info = self.upbit_api.get_order_info(order_result['uuid'])
                    order_state = order_info.get('state', 'unknown') if order_info else 'unknown'
                    executed_volume = float(order_info.get('executed_volume', 0)) if order_info else 0
                    logger.info(f"🔄 매도 재확인 결과: {market} → {order_state} (체결량: {executed_volume})")
                elif order_state == 'cancel':
                    # cancel 상태라도 부분 체결이 있을 수 있음
                    if executed_volume > 0:
                        logger.warning(f"🔄 {market} 매도 주문 취소되었지만 부분 체결됨: {executed_volume}")
                    else:
                        logger.warning(f"❌ {market} 매도 주문 취소: 체결량 0")
                else:
                    logger.warning(f"❓ 매도 예상치 못한 주문 상태: {market} → {order_state}")
            else:
                logger.error(f"❌ 매도 주문 정보 조회 실패: {market}")
            
            # 체결 여부 판단: 상태가 'done'이거나 executed_volume > 0이면 체결된 것으로 처리
            is_filled = order_info and (order_info.get('state') == 'done' or float(order_info.get('executed_volume', 0)) > 0)
            
            if is_filled:
                avg_price = float(order_info.get('avg_price', current_price))
                
                # 포지션 종료 및 손익 계산
                profit_loss = self.risk_manager.close_position(market, avg_price)
                
                if profit_loss is not None:
                    profit_rate = (profit_loss / position.investment_amount) * 100
                    
                    # 매도 알림
                    logger.info(f"📱 매도 텔레그램 알림 전송 시도: {market}")
                    notify_sell(market, avg_price, position.quantity * avg_price, 
                               profit_loss, profit_rate, reason)
                    
                    logger.warning(f"💸 매도 완료: {market}, 가격: {avg_price:,.0f}원, "
                               f"손익: {profit_loss:,.0f}원 ({profit_rate:+.2f}%)")
                    
                    # AI 추천 성과 추적 업데이트
                    self._update_ai_recommendation_exit(market, avg_price)
                else:
                    logger.error(f"포지션 종료 실패: {market}")
            else:
                # 상세한 실패 정보 제공
                if order_info:
                    final_state = order_info.get('state', 'unknown')
                    final_executed = float(order_info.get('executed_volume', 0))
                    if final_executed > 0:
                        logger.error(f"🔄 {market} 매도 부분 체결: 상태 {final_state}, 체결량 {final_executed} (포지션 종료 실패)")
                    else:
                        logger.error(f"💥 {market} 매도 주문 체결 실패: 상태 {final_state}, 체결량 0")
                    logger.debug(f"📊 매도 주문 세부정보: {order_info}")
                else:
                    logger.error(f"💥 {market} 매도 주문 정보 조회 불가 (API 응답 없음)")
                
        except Exception as e:
            logger.error(f"매도 실행 오류 ({market}): {e}")
    
    def get_status(self) -> Dict:
        """봇 현재 상태 반환"""
        return {
            'is_running': self.is_running,
            'is_paused': self.is_paused,
            'krw_balance': self.upbit_api.get_krw_balance(),
            'positions': self.risk_manager.get_position_summary(),
            'daily_pnl': self.risk_manager.get_daily_pnl(),
            'trading_stats': self.risk_manager.get_trading_stats()
        }
    
    def _analyze_position_swap(self, losing_positions: List[Dict]):
        """포지션 교체 분석 및 실행"""
        try:
            # 새로운 매수 기회 탐색
            markets = get_tradeable_markets()
            if not markets:
                return
            
            opportunities = []
            for market in markets[:15]:  # 상위 15개 시장만 확인
                try:
                    # 현재 보유중인 종목은 제외
                    current_positions = self.risk_manager.get_open_positions()
                    if market in current_positions:
                        continue
                    
                    current_price = self.upbit_api.get_current_price(market)
                    candle_data = self.upbit_api.get_candles(market, minutes=5, count=10)
                    if not current_price or not candle_data:
                        continue
                    
                    # 거래량 급등 확인
                    latest_volume = candle_data[0]['candle_acc_trade_volume']
                    avg_volume = sum(c['candle_acc_trade_volume'] for c in candle_data[1:6]) / 5
                    volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 1
                    
                    price_change = self.market_analyzer.get_price_change(market)
                    
                    if volume_ratio >= 2.0:  # 거래량 2배 이상 증가
                        # 거래대금 정보 추가
                        trade_amount = self._get_trade_amount(market)
                        opportunities.append({
                            'market': market,
                            'current_price': current_price,
                            'volume_ratio': volume_ratio,
                            'price_change': price_change or 0,
                            'trade_amount': trade_amount
                        })
                except Exception as e:
                    logger.debug(f"시장 데이터 조회 실패 ({market}): {e}")
                    continue
            
            if not opportunities:
                logger.info("📊 포지션 교체 기회 없음 - 새로운 매수 기회가 부족")
                return
            
            logger.info(f"🔍 포지션 교체 분석 중: 손실 포지션 {len(losing_positions)}개, 매수 기회 {len(opportunities)}개")
            
            # AI 포지션 교체 분석
            swap_analysis = self.ai_analyzer.analyze_position_swap(losing_positions, opportunities)
            
            if (swap_analysis.get('should_swap') and 
                swap_analysis.get('sell_market') and 
                swap_analysis.get('buy_market')):
                
                sell_market = swap_analysis['sell_market']
                buy_market = swap_analysis['buy_market']
                confidence = swap_analysis.get('confidence', 5)
                
                logger.info(f"🔄 AI 포지션 교체 결정 (신뢰도: {confidence}/10)")
                logger.info(f"📤 매도: {sell_market}")
                logger.info(f"📥 매수: {buy_market}")
                logger.info(f"💡 이유: {swap_analysis['reason']}")
                
                # 해당 손실 포지션 찾기
                sell_position = next((pos for pos in losing_positions if pos['market'] == sell_market), None)
                buy_opportunity = next((opp for opp in opportunities if opp['market'] == buy_market), None)
                
                # 동적 신뢰도 임계값 적용
                current_settings = self.get_current_settings()
                confidence_threshold = current_settings.get('ai_confidence_threshold', 7)
                
                if sell_position and buy_opportunity and confidence >= confidence_threshold:  # 동적 신뢰도 임계값 적용
                    # 손절매 실행
                    logger.info(f"🔸 손절매 실행: {sell_market}")
                    self._execute_sell(sell_market, sell_position['current_price'], 
                                     f"AI 포지션 교체 (손절, 신뢰도: {confidence})")
                    
                    # 잠시 대기 후 새로운 종목 매수
                    time.sleep(3)
                    logger.info(f"🔹 신규 매수 실행: {buy_market}")
                    current_settings = self.get_current_settings()
                    self._execute_buy(buy_opportunity, current_settings)
                    
                    logger.info(f"🎯 포지션 교체 완료: {sell_market} → {buy_market}")
                else:
                    logger.info(f"⚠️ 포지션 교체 취소: 신뢰도 부족 또는 종목 정보 오류 (신뢰도: {confidence}, 필요: {confidence_threshold})")
            else:
                logger.info("📊 AI 분석 결과: 포지션 교체 불필요")
                if swap_analysis.get('reason'):
                    logger.info(f"💡 이유: {swap_analysis['reason']}")
                    
        except Exception as e:
            logger.error(f"포지션 교체 분석 오류: {e}")

# 전역 봇 인스턴스
_bot: Optional[CoinButler] = None

def get_bot() -> CoinButler:
    """전역 봇 인스턴스 반환"""
    global _bot
    if _bot is None:
        _bot = CoinButler()
    return _bot

def main():
    """메인 실행 함수"""
    bot = get_bot()
    
    try:
        bot.start()
    except KeyboardInterrupt:
        logger.info("사용자 중단")
    except Exception as e:
        logger.error(f"실행 오류: {e}")
    finally:
        bot.stop()

if __name__ == "__main__":
    main()
