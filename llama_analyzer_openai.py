#!/usr/bin/env python3
"""
HYBRID MARKET ANALYZER
Three-tier AI validation for every trade signal:

  Tier 1 — OpenAI GPT-4o-mini  (primary, fast, ~0.5s, ~$0.001/call)
  Tier 2 — Ollama Llama 3       (fallback if OpenAI fails/unavailable)
  Tier 3 — Technical scoring    (pure rule-based, always available)

Startup log produced:
  ✅ OpenAI initialized (gpt-4o-mini) - primary analyzer
  ✅ Ollama initialized (fallback analyzer)
  🎯 Hybrid mode: OpenAI GPT (primary) → Llama (fallback) → Technical

Usage:
  from llama_analyzer_openai import HybridMarketAnalyzer
  analyzer = HybridMarketAnalyzer(use_openai=True)
  result   = analyzer.analyze_market(symbol, market_data, strategy)
  # result = {'signal': 'LONG', 'confidence': 0.82, 'reason': '...', 'tier': 'openai'}
"""

import os
import re
import json
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger('llama_analyzer_openai')

# ── OPTIONAL IMPORTS ──────────────────────────────────────────────────────────
try:
    from openai import OpenAI as _OpenAI
    OPENAI_PKG = True
except ImportError:
    _OpenAI    = None
    OPENAI_PKG = False

try:
    import ollama as _ollama
    OLLAMA_PKG = True
except ImportError:
    _ollama    = None
    OLLAMA_PKG = False

try:
    from setup_rag_database import (
        init_rag_database,
        get_strategy_context,
        get_pattern_context,
        log_signal_to_rag,
    )
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False
    def init_rag_database(*a, **kw): pass
    def get_strategy_context(*a, **kw): return {}
    def get_pattern_context(*a, **kw): return {}
    def log_signal_to_rag(*a, **kw): pass


# ==============================================================================
# CONFIG
# ==============================================================================

OPENAI_API_KEY   = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL     = 'gpt-4o-mini'
OLLAMA_MODEL     = os.getenv('LLAMA_MODEL', os.getenv('OLLAMA_MODEL', 'llama3.1:latest'))
OLLAMA_HOST      = os.getenv('OLLAMA_URL', os.getenv('OLLAMA_HOST', 'http://localhost:11434'))
MIN_CONFIDENCE   = 0.60
TECHNICAL_PASS_SCORE = 65


def _current_session() -> str:
    from datetime import timezone, timedelta as td
    et = datetime.now(timezone.utc).astimezone(timezone(td(hours=-5)))
    h  = et.hour + et.minute / 60.0
    if   9.5  <= h < 16.0: return 'NY'
    elif 3.0  <= h <  9.5: return 'London'
    elif 18.0 <= h or h < 3.0: return 'Asia'
    return 'UAE'


# ==============================================================================
# PROMPT BUILDER  (uses RAG context when available)
# ==============================================================================

def _build_prompt(symbol: str, market_data: dict, strategy: str) -> str:
    """
    Build a validation prompt — NOT a direction-prediction prompt.

    The signal has ALREADY passed: R:R check, MTF alignment, candlestick,
    whipsaw protection, volatility range, and quality score.
    AI's ONLY job is to catch critical flaws that technical filters miss.
    Default behavior must be APPROVAL of the incoming direction.
    """
    action    = market_data.get('action', 'LONG')
    quality   = market_data.get('quality_score', 0)
    pattern   = market_data.get('pattern', market_data.get('pattern_name', 'ema_crossover')) or 'ema_crossover'
    session   = _current_session()

    def _fmt(val, name: str) -> str:
        if val is None or val == 'N/A' or val == '' or str(val) == '0':
            return f"  {name}: not_provided (do not use as rejection criterion)"
        return f"  {name}: {val}"

    adx_raw   = market_data.get('adx') or market_data.get('adx_1m')
    rsi_raw   = market_data.get('rsi') or market_data.get('rsi_1m')
    atr_raw   = market_data.get('atr') or market_data.get('atr_1m')
    vix_raw   = market_data.get('vix')
    vol_ratio = market_data.get('volume_ratio')
    spy_trend = market_data.get('spy_trend', 'unknown')

    indicator_block = "\n".join([
        _fmt(adx_raw,   'ADX'),
        _fmt(rsi_raw,   'RSI'),
        _fmt(atr_raw,   'ATR'),
        _fmt(vix_raw,   'VIX'),
        _fmt(vol_ratio, 'Volume ratio'),
        f"  SPY trend: {spy_trend}",
    ])

    is_orb = any(x in strategy.upper() for x in ('ORB', 'BREAKOUT', 'BREAK', 'RANGE'))
    orb_note = (
        "\n\nIMPORTANT: This is an Opening Range Breakout (ORB) strategy. "
        "ORB trades the breakout direction regardless of broader market trend. "
        "NEVER reject based on SPY trend, macro sentiment, or counter-trend concerns."
    ) if is_orb else ""

    strat_ctx = get_strategy_context(strategy, symbol)
    pat_ctx   = get_pattern_context(pattern, symbol, session)
    rag_lines = []
    if strat_ctx and strat_ctx.get('win_rate', 0) > 0:
        rag_lines.append(f"Strategy historical win rate: {strat_ctx['win_rate']:.0%}")
    if pat_ctx and pat_ctx.get('win_rate', 0) > 0:
        rag_lines.append(
            f"Pattern '{pattern}' on {symbol}/{session}: "
            f"{pat_ctx.get('win_rate',0):.0%} win rate, "
            f"{pat_ctx.get('reliability',0)}/100 reliability"
        )
    rag_block = ("\n\nHistorical context (reference only):\n" +
                 "\n".join(f"  • {l}" for l in rag_lines)) if rag_lines else ""

    return f"""You are a trade signal VALIDATOR for a futures trading system.

This {action} signal on {symbol} has ALREADY passed all technical filters:
  R:R >= 2:1, MTF alignment (3/3), candlestick color, whipsaw protection,
  volatility range check, and quality score {quality}/100.

Signal context:
  Symbol:   {symbol}
  Direction: {action}
  Strategy:  {strategy}
  Pattern:   {pattern}
  Session:   {session}
{indicator_block}{rag_block}{orb_note}

YOUR ROLE — catch CRITICAL flaws only:
  ✅ APPROVE ({action}) by default — the signal passed all technical checks.
  🚫 REJECT only if you identify a SPECIFIC, HIGH-CONFIDENCE critical flaw:
       • RSI > 85 for a LONG signal (extreme overbought)
       • RSI < 15 for a SHORT signal (extreme oversold)
       • VIX > 35 for an equity LONG (extreme panic, only if VIX is provided)
       • Pattern directly contradicts the strategy (e.g., bearish engulfing for LONG)

STRICT RULES:
  1. Missing/not_provided data = DO NOT use as rejection reason.
  2. Broad market trend (SPY down, bearish sentiment) = NOT a reason to reject.
  3. Low ADX or missing ADX = NOT a reason to reject.
  4. Your default answer MUST be {action} with confidence 0.65-0.75.
  5. Only exceed confidence 0.80 if you have an overwhelming specific reason.

Respond ONLY with valid JSON — no markdown, no extra text:
{{"signal": "LONG", "confidence": 0.65, "reason": "one sentence explaining decision"}}"""


# ==============================================================================
# TIER 1 — OPENAI
# ==============================================================================

class _OpenAIAnalyzer:
    def __init__(self, api_key: str):
        self.client = _OpenAI(api_key=api_key)
        self.model  = OPENAI_MODEL
        self.calls  = 0
        self.errors = 0

    def analyze(self, symbol: str, market_data: dict, strategy: str) -> Optional[dict]:
        prompt = _build_prompt(symbol, market_data, strategy)
        try:
            t0   = time.time()
            resp = self.client.chat.completions.create(
                model       = self.model,
                messages    = [
                    {"role": "system", "content": "You are a concise futures trading expert. Respond only with JSON."},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens  = 80,
                temperature = 0.05,
                timeout     = 4,
            )
            elapsed = time.time() - t0
            self.calls += 1
            raw = resp.choices[0].message.content.strip()
            return _parse_response(raw, 'openai', elapsed)
        except Exception as e:
            self.errors += 1
            logger.debug(f"OpenAI error: {e}")
            return None


# ==============================================================================
# TIER 2 — OLLAMA (local Llama)
# ==============================================================================

class _OllamaAnalyzer:
    def __init__(self):
        self.model  = OLLAMA_MODEL
        self.host   = OLLAMA_HOST
        self.calls  = 0
        self.errors = 0

    def analyze(self, symbol: str, market_data: dict, strategy: str) -> Optional[dict]:
        prompt = _build_prompt(symbol, market_data, strategy)
        try:
            t0   = time.time()
            resp = _ollama.chat(
                model   = self.model,
                messages= [{'role': 'user', 'content': prompt}],
                options = {'temperature': 0.05, 'num_predict': 80},
            )
            elapsed = time.time() - t0
            self.calls += 1
            raw = resp['message']['content'].strip()
            return _parse_response(raw, 'ollama', elapsed)
        except Exception as e:
            self.errors += 1
            logger.debug(f"Ollama error: {e}")
            return None


# ==============================================================================
# TIER 3 — TECHNICAL (pure rule-based, never fails)
# ==============================================================================

def _technical_analyze(symbol: str, market_data: dict, strategy: str) -> dict:
    """
    Rule-based fallback scoring — always returns a result.
    Uses quality_score, ADX, RSI, pattern reliability, session fit.

    CRITICAL: Default signal is always `action` (the incoming direction).
    We only flip direction if there is a clear, specific technical contradiction.
    Missing data (ADX=0, RSI=None) must NOT cause rejection.
    """
    action    = market_data.get('action', 'LONG')
    quality   = float(market_data.get('quality_score', 0))
    adx_raw   = market_data.get('adx') or market_data.get('adx_1m') or 0
    adx       = float(adx_raw) if adx_raw else 0.0
    rsi_raw   = market_data.get('rsi') or market_data.get('rsi_1m') or 50
    rsi       = float(rsi_raw) if rsi_raw else 50.0
    pattern   = market_data.get('pattern', 'ema_crossover') or 'ema_crossover'
    session   = _current_session()

    score = 0.0
    notes = []

    score += min(quality / 100.0, 1.0) * 0.40
    notes.append(f"quality={quality:.0f}")

    if adx > 35:
        score += 0.25
        notes.append(f"ADX strong ({adx:.0f})")
    elif adx > 25:
        score += 0.18
        notes.append(f"ADX moderate ({adx:.0f})")
    elif adx > 0:
        score += 0.08
        notes.append(f"ADX weak ({adx:.0f})")
    else:
        score += 0.12
        notes.append("ADX not_provided (neutral)")

    if action == 'LONG':
        rsi_ok  = 35 <= rsi <= 70
        rsi_bad = rsi > 85
    else:
        rsi_ok  = 30 <= rsi <= 65
        rsi_bad = rsi < 15
    if rsi_ok:
        score += 0.15
        notes.append(f"RSI ok ({rsi:.0f})")
    elif not rsi_bad:
        score += 0.07
        notes.append(f"RSI neutral ({rsi:.0f})")

    pat_ctx = get_pattern_context(pattern, symbol, session)
    if pat_ctx:
        reliability = pat_ctx.get('reliability', 65) / 100.0
        score += reliability * 0.20
        notes.append(f"pattern {pattern}@{pat_ctx.get('reliability',65)}")
    else:
        score += 0.65 * 0.20

    confidence = min(score, 0.98)

    adx_raw_val = market_data.get('adx') or market_data.get('adx_1m') or 0
    rsi_raw_val = market_data.get('rsi') or market_data.get('rsi_1m') or 50
    extreme_rsi_contra = (action == 'LONG' and float(rsi_raw_val or 50) > 85) or \
                         (action == 'SHORT' and float(rsi_raw_val or 50) < 15)

    if extreme_rsi_contra:
        signal = 'SHORT' if action == 'LONG' else 'LONG'
        reason = f"Technical: EXTREME RSI contradiction ({rsi:.0f}) — {', '.join(notes)}"
    else:
        signal = action
        reason = f"Technical: {', '.join(notes)}"

    return {
        'signal':     signal,
        'confidence': round(max(confidence, 0.55), 3),
        'reason':     reason,
        'tier':       'technical',
        'elapsed':    0.0,
    }


# ==============================================================================
# RESPONSE PARSER
# ==============================================================================

def _parse_response(raw: str, tier: str, elapsed: float) -> Optional[dict]:
    """Extract signal/confidence from AI JSON response, robust to malformed output."""
    try:
        clean = re.sub(r'```(?:json)?', '', raw).strip()
        data  = json.loads(clean)
        sig   = data.get('signal', '').upper()
        conf  = float(data.get('confidence', 0))
        reason= data.get('reason', '')

        if sig not in ('LONG', 'SHORT') or not (0.0 <= conf <= 1.0):
            raise ValueError(f"Bad values: signal={sig} conf={conf}")

        return {'signal': sig, 'confidence': conf, 'reason': reason,
                'tier': tier, 'elapsed': round(elapsed, 2)}

    except Exception as e:
        logger.debug(f"Parse error ({tier}): {e} | raw={raw[:80]}")
        sig_m  = re.search(r'\b(LONG|SHORT)\b', raw, re.I)
        conf_m = re.search(r'\b(0\.\d+|1\.0)\b', raw)
        if sig_m and conf_m:
            return {
                'signal':     sig_m.group(1).upper(),
                'confidence': float(conf_m.group(1)),
                'reason':     'parsed from text',
                'tier':       tier,
                'elapsed':    round(elapsed, 2),
            }
        return None


# ==============================================================================
# HYBRID MARKET ANALYZER  (public class used by ultimate_entry_validator)
# ==============================================================================

class HybridMarketAnalyzer:
    """
    Three-tier AI analyzer:
      OpenAI GPT-4o-mini  → fast, cheap, accurate
      Ollama Llama 3      → local fallback (if OpenAI unavailable/fails)
      Technical scoring   → always-available rule-based fallback

    Returns:
      {'signal': 'LONG'|'SHORT', 'confidence': 0.0-1.0,
       'reason': str, 'tier': 'openai'|'ollama'|'technical'}
    """

    def __init__(self, use_openai: bool = True):
        self._openai   = None
        self._ollama   = None
        self._mode     = 'technical'
        self.stats     = {'openai': 0, 'ollama': 0, 'technical': 0, 'errors': 0}

        if RAG_AVAILABLE:
            try:
                init_rag_database()
            except Exception as e:
                logger.debug(f"RAG init error: {e}")

        # Tier 1 — OpenAI
        openai_ok = False
        if use_openai and OPENAI_PKG and OPENAI_API_KEY:
            try:
                self._openai = _OpenAIAnalyzer(OPENAI_API_KEY)
                self._openai.client.models.list()
                logger.info(f"✅ OpenAI initialized ({OPENAI_MODEL}) - primary analyzer")
                openai_ok = True
            except Exception as e:
                logger.warning(f"⚠️  OpenAI unavailable: {e}")
                self._openai = None
        elif use_openai and not OPENAI_API_KEY:
            logger.warning("⚠️  OPENAI_API_KEY not set — OpenAI tier disabled")
        elif use_openai and not OPENAI_PKG:
            logger.warning("⚠️  openai package not installed — pip install openai")

        # Tier 2 — Ollama
        ollama_ok = False
        if OLLAMA_PKG:
            try:
                self._ollama = _OllamaAnalyzer()
                _ollama.list()
                logger.info("✅ Ollama initialized (fallback analyzer)")
                ollama_ok = True
            except Exception as e:
                logger.info(f"ℹ️  Ollama not running ({e}) — will use technical fallback")
                self._ollama = None
        else:
            logger.info("ℹ️  ollama package not installed — technical fallback active")

        # Set mode
        if openai_ok and ollama_ok:
            self._mode = 'hybrid_full'
            logger.info("🎯 Hybrid mode: OpenAI GPT (primary) → Llama (fallback) → Technical")
        elif openai_ok:
            self._mode = 'hybrid_openai'
            logger.info("🎯 Mode: OpenAI GPT (primary) → Technical fallback")
        elif ollama_ok:
            self._mode = 'hybrid_ollama'
            logger.info("🎯 Mode: Llama (primary) → Technical fallback")
        else:
            self._mode = 'technical'
            logger.info("🎯 Mode: Technical scoring only (AI services unavailable)")

    def analyze_market(self, symbol: str, market_data: dict,
                       strategy: str = 'UNKNOWN') -> dict:
        """Run tiered analysis. Falls through to next tier on failure."""
        # Tier 1: OpenAI
        if self._openai:
            result = self._openai.analyze(symbol, market_data, strategy)
            if result:
                self.stats['openai'] += 1
                self._log_to_rag(symbol, market_data, strategy, result)
                logger.debug(f"🤖 OpenAI: {result['signal']} {result['confidence']:.0%} [{result['elapsed']}s]")
                return result

        # Tier 2: Ollama
        if self._ollama:
            result = self._ollama.analyze(symbol, market_data, strategy)
            if result:
                self.stats['ollama'] += 1
                self._log_to_rag(symbol, market_data, strategy, result)
                logger.debug(f"🦙 Ollama: {result['signal']} {result['confidence']:.0%} [{result['elapsed']}s]")
                return result

        # Tier 3: Technical
        result = _technical_analyze(symbol, market_data, strategy)
        self.stats['technical'] += 1
        self._log_to_rag(symbol, market_data, strategy, result)
        logger.debug(f"📊 Technical: {result['signal']} {result['confidence']:.0%}")
        return result

    def _log_to_rag(self, symbol: str, market_data: dict,
                    strategy: str, result: dict):
        try:
            log_signal_to_rag(
                strategy     = strategy,
                instrument   = symbol,
                action       = market_data.get('action', 'LONG'),
                quality_score= int(market_data.get('quality_score', 0)),
                pattern      = market_data.get('pattern', ''),
                ai_approved  = result['confidence'] >= MIN_CONFIDENCE,
                ai_confidence= result['confidence'],
            )
        except Exception as e:
            logger.debug(f"RAG log error: {e}")

    def get_stats(self) -> dict:
        total = sum(self.stats.values()) or 1
        return {
            'mode':      self._mode,
            'total':     total,
            'openai':    f"{self.stats['openai']} ({self.stats['openai']/total:.0%})",
            'ollama':    f"{self.stats['ollama']} ({self.stats['ollama']/total:.0%})",
            'technical': f"{self.stats['technical']} ({self.stats['technical']/total:.0%})",
        }


# ==============================================================================
# STANDALONE TEST
# ==============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    print("\n🧪 Testing HybridMarketAnalyzer...")
    analyzer = HybridMarketAnalyzer(use_openai=True)

    test_signal = {
        'action':       'LONG',
        'quality_score': 82,
        'adx':           32.5,
        'rsi':           48.0,
        'atr':           12.0,
        'pattern':       'liquidity_sweep',
        'volume_ratio':  1.8,
        'spy_trend':     'bullish',
        'vix':           18.2,
    }
    result = analyzer.analyze_market('MES', test_signal, 'MES-5M')
    print(f"\n📊 Result: {result}")
    print(f"📈 Stats:  {analyzer.get_stats()}")
