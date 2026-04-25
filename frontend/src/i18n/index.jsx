import React, { createContext, useContext, useState, useCallback } from "react";
import translations from "./translations";

const I18nContext = createContext();

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(() => {
    try {
      return localStorage.getItem("app_lang") || "en";
    } catch {
      return "en";
    }
  });

  const switchLang = useCallback((newLang) => {
    setLang(newLang);
    try {
      localStorage.setItem("app_lang", newLang);
    } catch {}
  }, []);

  const t = useCallback(
    (key) => {
      return (
        (translations[lang] && translations[lang][key]) ||
        translations["en"][key] ||
        key
      );
    },
    [lang],
  );

  return (
    <I18nContext.Provider value={{ lang, switchLang, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
