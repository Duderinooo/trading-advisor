"""MemPalace integration for trade history and pattern memory."""

import logging
import json
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from mempalace.palace import get_collection
    from mempalace.miner import add_drawer
    from mempalace.layers import Layer3
    from mempalace.knowledge_graph import KnowledgeGraph
    MEMPALACE_AVAILABLE = True
except ImportError as e:
    MEMPALACE_AVAILABLE = False
    logger.warning("MemPalace not available: %s", e)

PALACE_DIR = Path(__file__).parent / ".palace"
_AGENT = "trading_bot"


def _collection():
    if not MEMPALACE_AVAILABLE:
        return None
    PALACE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return get_collection(str(PALACE_DIR), create=True)
    except Exception as e:
        logger.warning("Palace collection error: %s", e)
        return None


def _searcher() -> "Layer3 | None":
    if not MEMPALACE_AVAILABLE:
        return None
    try:
        return Layer3(str(PALACE_DIR))
    except Exception as e:
        logger.warning("Layer3 init error: %s", e)
        return None


# =============================================================================
# Trade Logging
# =============================================================================

def log_trade(trade: dict, action: str, context: str = None):
    col = _collection()
    if col is None:
        return

    ticker = trade.get("ticker", "UNKNOWN")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    content = f"=== TRADE {action}: {ticker} ===\nTimestamp: {timestamp}\n\n{json.dumps(trade, indent=2)}"
    if context:
        content += f"\n\nContext: {context}"

    try:
        chunk_idx = int(time.time())
        add_drawer(col, wing="trades", room=ticker.lower().replace(".", "_"),
                   content=content, source_file=f"bot_trade_{ticker}", chunk_index=chunk_idx, agent=_AGENT)
        logger.info("Trade logged: %s %s", action, ticker)
    except Exception as e:
        logger.warning("Failed to log trade: %s", e)


def log_analysis(analysis: str, mode: str, event_context: str = None):
    col = _collection()
    if col is None:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"=== ANALYSIS ({mode.upper()}) ===\nTimestamp: {timestamp}\n"
    if event_context:
        content += f"Trigger: {event_context}\n"
    content += f"\n{analysis}"

    try:
        chunk_idx = int(time.time())
        add_drawer(col, wing="analyses", room=mode,
                   content=content, source_file=f"bot_analysis_{mode}", chunk_index=chunk_idx, agent=_AGENT)
    except Exception as e:
        logger.warning("Failed to log analysis: %s", e)


# =============================================================================
# History Retrieval
# =============================================================================

def search_trade_history(query: str, limit: int = 5) -> list[str]:
    s = _searcher()
    if s is None:
        return []
    try:
        hits = s.search_raw(query, wing="trades", n_results=limit)
        return [h["text"] for h in hits]
    except Exception as e:
        logger.warning("Search failed: %s", e)
        return []


def search_analyses(query: str, limit: int = 3) -> list[str]:
    s = _searcher()
    if s is None:
        return []
    try:
        hits = s.search_raw(query, wing="analyses", n_results=limit)
        return [h["text"] for h in hits]
    except Exception as e:
        logger.warning("Search failed: %s", e)
        return []


def get_ticker_history(ticker: str, limit: int = 10) -> list[str]:
    s = _searcher()
    if s is None:
        return []
    try:
        room = ticker.lower().replace(".", "_")
        hits = s.search_raw(f"trades {ticker}", wing="trades", room=room, n_results=limit)
        return [h["text"] for h in hits]
    except Exception as e:
        logger.warning("Failed to get ticker history: %s", e)
        return []


def get_similar_market_situations(description: str, limit: int = 3) -> list[str]:
    s = _searcher()
    if s is None:
        return []
    try:
        hits = s.search_raw(description, wing="analyses", n_results=limit)
        return [h["text"] for h in hits]
    except Exception as e:
        logger.warning("Search failed: %s", e)
        return []


# =============================================================================
# Knowledge Graph
# =============================================================================

def _kg() -> "KnowledgeGraph | None":
    if not MEMPALACE_AVAILABLE:
        return None
    try:
        kg_path = str(PALACE_DIR / "knowledge_graph.sqlite3")
        return KnowledgeGraph(db_path=kg_path)
    except Exception as e:
        logger.warning("KG init error: %s", e)
        return None


def add_ticker_fact(ticker: str, fact: str, valid_until: str = None):
    kg = _kg()
    if kg is None:
        return
    try:
        kg.add_triple(subject=ticker, predicate="has_fact", object=fact, valid_to=valid_until)
    except Exception as e:
        logger.warning("Failed to add fact: %s", e)


def get_ticker_facts(ticker: str) -> list[str]:
    kg = _kg()
    if kg is None:
        return []
    try:
        results = kg.query_entity(ticker)
        if not results:
            return []
        if isinstance(results, str):
            return [results] if results.strip() else []
        return [str(r) for r in results]
    except Exception as e:
        logger.warning("Failed to get facts: %s", e)
        return []


def log_pattern(pattern_name: str, description: str, success: bool, ticker: str = None):
    col = _collection()
    if col is None:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    outcome = "SUCCESS" if success else "FAILED"
    content = (
        f"=== PATTERN: {pattern_name} ({outcome}) ===\n"
        f"Timestamp: {timestamp}\nTicker: {ticker or 'N/A'}\n"
        f"Description: {description}\nOutcome: {outcome}"
    )
    try:
        add_drawer(col, wing="patterns", room=pattern_name,
                   content=content, source_file=f"bot_pattern_{pattern_name}",
                   chunk_index=int(time.time()), agent=_AGENT)
    except Exception as e:
        logger.warning("Failed to log pattern: %s", e)


# =============================================================================
# Context Building for Claude
# =============================================================================

def build_history_context(ticker: str = None, query: str = None, include_recent: bool = False) -> str:
    parts = []

    # Generic "recent analyses" search returns different strings every call — it busts
    # Anthropic's prompt cache. Opt-in only.
    if include_recent:
        recent = search_analyses("recent trading analysis", limit=2)
        if recent:
            parts.append("## Recent Analyses\n" + "\n---\n".join(recent[:2]))

    if ticker:
        history = get_ticker_history(ticker, limit=3)
        if history:
            parts.append(f"## {ticker} Trade History\n" + "\n---\n".join(history[:3]))
        facts = get_ticker_facts(ticker)
        if facts:
            parts.append(f"## Known Facts about {ticker}\n" + "\n".join(f"- {f}" for f in facts))

    if query:
        similar = get_similar_market_situations(query, limit=2)
        if similar:
            parts.append("## Similar Past Situations\n" + "\n---\n".join(similar[:2]))

    return "\n\n".join(parts)


# =============================================================================
# Initialization
# =============================================================================

def init_palace() -> bool:
    col = _collection()
    if col is not None:
        logger.info("MemPalace initialized at %s", PALACE_DIR)
        return True
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if init_palace():
        logger.info("Palace ready — running smoke test")
        log_trade({"ticker": "TEST", "entry_price": 100.0, "stop_loss": 95.0}, "OPEN", "smoke test")
        results = search_trade_history("TEST")
        logger.info("Search returned %d results", len(results))
