import React, { useState, useEffect, useRef } from "react";
import { useI18n } from "../i18n";
import { fetchTrades } from "../services/marketDataService";

const RecentTrades = ({ symbol }) => {
  const { t } = useI18n();
  const [trades, setTrades] = useState([]);
  const [error, setError] = useState(null);
  const intervalRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetchTrades(symbol, 50)
        .then((data) => {
          if (cancelled) return;
          setTrades(data);
          setError(null);
        })
        .catch(() => {
          if (!cancelled) setError("Failed to load trades");
        });
    };
    load();
    intervalRef.current = setInterval(load, 3000);
    return () => {
      cancelled = true;
      clearInterval(intervalRef.current);
    };
  }, [symbol]);

  const formatTime = (ts) => {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  };

  const f = (v) =>
    v.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });

  return (
    <div className="h-full flex flex-col text-xs font-mono overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700">
        <span className="text-gray-400 font-sans font-medium text-sm">
          {t("recentTrades")}
        </span>
        <span className="text-gray-500">{symbol}</span>
      </div>

      {/* Column headers */}
      <div className="flex px-3 py-1 text-gray-500 border-b border-gray-700">
        <span className="w-20">{t("time")}</span>
        <span className="flex-1">{t("price")}</span>
        <span className="flex-1 text-right">{t("amount")}</span>
        <span className="w-12 text-right">{t("side")}</span>
      </div>

      {/* Trade list */}
      <div className="flex-1 overflow-y-auto">
        {error && trades.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <p className="text-red-400 text-xs">{error}</p>
          </div>
        )}
        {trades.map((trade, i) => (
          <div
            key={i}
            className="flex px-3 py-0.5 hover:bg-gray-800 transition-colors"
          >
            <span className="w-20 text-gray-500">{formatTime(trade.time)}</span>
            <span
              className={`flex-1 ${trade.side === "buy" ? "text-green-400" : "text-red-400"}`}
            >
              {f(trade.price)}
            </span>
            <span className="flex-1 text-right text-gray-300">
              {trade.volume}
            </span>
            <span
              className={`w-12 text-right font-sans ${trade.side === "buy" ? "text-green-400" : "text-red-400"}`}
            >
              {t(trade.side)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default RecentTrades;
