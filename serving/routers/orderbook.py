import json
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException

from serving.connections import get_redis

router = APIRouter(prefix="/api", tags=["orderbook"])


def _fetch_binance_orderbook(symbol: str, limit: int = 50) -> dict | None:
    params = urlencode({"symbol": symbol, "limit": limit})
    url = f"https://api.binance.com/api/v3/depth?{params}"
    try:
        with urlopen(url, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        bids = [[float(p), float(q)] for p, q in payload.get("bids", [])]
        asks = [[float(p), float(q)] for p, q in payload.get("asks", [])]
        best_bid = float(bids[0][0]) if bids else 0.0
        best_ask = float(asks[0][0]) if asks else 0.0
        return {
            "bids": bids,
            "asks": asks,
            "spread": round(best_ask - best_bid, 8) if (best_bid and best_ask) else 0.0,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "event_time": int(time.time() * 1000),
        }
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


@router.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str):
    symbol_u = symbol.upper()
    r = await get_redis()
    data = await r.hgetall(f"orderbook:{symbol_u}")
    if not data:
        ticker = await r.hgetall(f"ticker:latest:{symbol_u}")
        if ticker:
            bid = float(ticker.get("bid", 0) or 0)
            ask = float(ticker.get("ask", 0) or 0)
            event_time = int(float(ticker.get("event_time", 0) or 0))
            if bid > 0 and ask > 0:
                return {
                    "symbol": symbol_u,
                    "bids": [[bid, 0.0]],
                    "asks": [[ask, 0.0]],
                    "spread": round(ask - bid, 8),
                    "best_bid": bid,
                    "best_ask": ask,
                    "event_time": event_time,
                }

        fallback = _fetch_binance_orderbook(symbol_u)
        if not fallback:
            raise HTTPException(404, f"No order book for {symbol}")

        # Warm cache for clients that poll frequently.
        await r.hset(
            f"orderbook:{symbol_u}",
            mapping={
                "bids": json.dumps(fallback["bids"]),
                "asks": json.dumps(fallback["asks"]),
                "spread": fallback["spread"],
                "best_bid": fallback["best_bid"],
                "best_ask": fallback["best_ask"],
                "event_time": fallback["event_time"],
            },
        )
        await r.expire(f"orderbook:{symbol_u}", 30)
        return {"symbol": symbol_u, **fallback}

    return {
        "symbol": symbol_u,
        "bids": json.loads(data.get("bids", "[]")),
        "asks": json.loads(data.get("asks", "[]")),
        "spread": float(data.get("spread", 0)),
        "best_bid": float(data.get("best_bid", 0)),
        "best_ask": float(data.get("best_ask", 0)),
        "event_time": int(float(data.get("event_time", 0))),
    }
