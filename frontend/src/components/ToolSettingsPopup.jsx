/**
 * ToolSettingsPopup.js
 * Settings pop-up panel for drawing tools (Elliott Wave, Harmonic ABCD,
 * Fibonacci Retracement, Trendline, etc.)
 */
import React, { useRef, useEffect } from 'react';
import { X } from 'lucide-react';

// Default settings per tool
export const DEFAULT_TOOL_SETTINGS = {
  trendline: {
    color: '#3b82f6',
    lineWidth: 2,
    showLabel: true,
    dashArray: 'solid',
  },
  horizontal: {
    color: '#22c55e',
    lineWidth: 1.5,
    showLabel: true,
    dashArray: 'dashed',
  },
  rectangle: {
    color: '#8b5cf6',
    lineWidth: 1.5,
    showLabel: false,
    fillOpacity: 0.1,
  },
  fibRetracement: {
    color: '#facc15',
    lineWidth: 1,
    showLabel: true,
    levels: [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1],
  },
  ruler: {
    color: '#facc15',
    lineWidth: 2,
    showLabel: true,
    dashArray: 'dashed',
  },
  elliottWave: {
    color: '#f97316',
    lineWidth: 2,
    showLabel: true,
    waveType: 'impulse', // 'impulse' | 'corrective'
  },
  harmonicABCD: {
    color: '#a855f7',
    lineWidth: 2,
    showLabel: true,
    fiboLevels: [0.618, 1.272],
  },
};

const DASH_OPTIONS = [
  { value: 'solid',  label: 'Liền nét' },
  { value: 'dashed', label: 'Nét đứt' },
  { value: 'dotted', label: 'Chấm' },
];

const FieldRow = ({ label, children }) => (
  <div className="flex items-center justify-between gap-3 py-1.5">
    <span className="text-xs text-gray-400 whitespace-nowrap">{label}</span>
    {children}
  </div>
);

const ToolSettingsPopup = ({ tool, settings, onChange, onClose, anchorRef }) => {
  const panelRef = useRef(null);

  // Position the popup near the toolbar button
  useEffect(() => {
    if (!anchorRef?.current || !panelRef.current) return;
    const btn = anchorRef.current.getBoundingClientRect();
    const panel = panelRef.current;
    panel.style.top  = `${btn.top}px`;
    panel.style.left = `${btn.right + 8}px`;
  }, [anchorRef]);

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target) &&
          anchorRef?.current && !anchorRef.current.contains(e.target)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose, anchorRef]);

  if (!settings) return null;

  const set = (key, value) => onChange({ ...settings, [key]: value });

  const toolTitles = {
    trendline:      'Đường xu hướng',
    horizontal:     'Đường ngang',
    rectangle:      'Hình chữ nhật',
    fibRetracement: 'Fibonacci',
    ruler:          'Thước kẻ',
    elliottWave:    'Sóng Elliott',
    harmonicABCD:   'Harmonic ABCD',
  };

  return (
    <div
      ref={panelRef}
      className="fixed z-[200] w-64 bg-gray-800 border border-gray-600 rounded-lg shadow-2xl p-3"
      style={{ minWidth: 220 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-2 pb-2 border-b border-gray-700">
        <span className="text-sm font-semibold text-white">{toolTitles[tool] || tool}</span>
        <button onClick={onClose} className="text-gray-400 hover:text-white transition-colors">
          <X size={14} />
        </button>
      </div>

      {/* Common fields */}
      <FieldRow label="Màu đường">
        <input
          type="color"
          value={settings.color}
          onChange={(e) => set('color', e.target.value)}
          className="w-8 h-6 rounded cursor-pointer border-0 bg-transparent"
        />
      </FieldRow>

      <FieldRow label="Độ dày (px)">
        <input
          type="range" min="0.5" max="5" step="0.5"
          value={settings.lineWidth}
          onChange={(e) => set('lineWidth', parseFloat(e.target.value))}
          className="w-28 accent-blue-500"
        />
        <span className="text-xs text-gray-300 w-5 text-right">{settings.lineWidth}</span>
      </FieldRow>

      {settings.dashArray !== undefined && (
        <FieldRow label="Kiểu nét">
          <select
            value={settings.dashArray}
            onChange={(e) => set('dashArray', e.target.value)}
            className="bg-gray-700 text-white text-xs rounded px-2 py-1 border border-gray-600 focus:outline-none"
          >
            {DASH_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </FieldRow>
      )}

      {settings.showLabel !== undefined && (
        <FieldRow label="Hiện nhãn">
          <button
            onClick={() => set('showLabel', !settings.showLabel)}
            className={`w-10 h-5 rounded-full transition-colors ${settings.showLabel ? 'bg-blue-600' : 'bg-gray-600'}`}
          >
            <span className={`block w-4 h-4 rounded-full bg-white shadow transition-transform mx-0.5 ${settings.showLabel ? 'translate-x-5' : 'translate-x-0'}`} />
          </button>
        </FieldRow>
      )}

      {settings.fillOpacity !== undefined && (
        <FieldRow label="Độ mờ nền">
          <input
            type="range" min="0" max="0.5" step="0.05"
            value={settings.fillOpacity}
            onChange={(e) => set('fillOpacity', parseFloat(e.target.value))}
            className="w-28 accent-blue-500"
          />
          <span className="text-xs text-gray-300 w-8 text-right">{Math.round(settings.fillOpacity * 100)}%</span>
        </FieldRow>
      )}

      {/* Elliott Wave specific */}
      {tool === 'elliottWave' && (
        <FieldRow label="Loại sóng">
          <div className="flex gap-1">
            <button
              onClick={() => set('waveType', 'impulse')}
              className={`text-xs px-2 py-0.5 rounded transition-colors ${settings.waveType === 'impulse' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300'}`}
            >1-2-3-4-5</button>
            <button
              onClick={() => set('waveType', 'corrective')}
              className={`text-xs px-2 py-0.5 rounded transition-colors ${settings.waveType === 'corrective' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300'}`}
            >A-B-C</button>
          </div>
        </FieldRow>
      )}

      {/* Harmonic ABCD specific */}
      {tool === 'harmonicABCD' && (
        <>
          <div className="mt-2 text-xs text-gray-400 mb-1">Tỷ lệ Fibonacci:</div>
          {settings.fiboLevels.map((lv, i) => (
            <FieldRow key={i} label={['AB', 'BC', 'CD', 'AD'][i] || `L${i}`}>
              <input
                type="number" min="0.1" max="3" step="0.001"
                value={lv}
                onChange={(e) => {
                  const newLevels = [...settings.fiboLevels];
                  newLevels[i] = parseFloat(e.target.value) || lv;
                  set('fiboLevels', newLevels);
                }}
                className="w-16 bg-gray-700 text-white text-xs rounded px-2 py-1 border border-gray-600 focus:outline-none"
              />
            </FieldRow>
          ))}
        </>
      )}

      {/* Fibonacci levels */}
      {tool === 'fibRetracement' && settings.levels && (
        <>
          <div className="mt-2 text-xs text-gray-400 mb-1">Mức Fibonacci:</div>
          <div className="grid grid-cols-4 gap-1">
            {[0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map((lv) => {
              const active = settings.levels.includes(lv);
              return (
                <button
                  key={lv}
                  onClick={() => {
                    const next = active
                      ? settings.levels.filter((x) => x !== lv)
                      : [...settings.levels, lv].sort((a, b) => a - b);
                    set('levels', next);
                  }}
                  className={`text-xs py-0.5 rounded transition-colors ${active ? 'bg-yellow-600 text-white' : 'bg-gray-700 text-gray-400'}`}
                >
                  {(lv * 100).toFixed(1)}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
};

export default ToolSettingsPopup;
