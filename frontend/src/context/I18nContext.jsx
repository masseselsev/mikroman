import React, { createContext, useContext, useState, useEffect } from 'react';
import { translations } from '../i18n/translations';

const I18nContext = createContext({
  lang: 'en',
  setLang: (l) => {},
  t: (key, params) => key
});

export const I18nProvider = ({ children }) => {
  const [lang, setLang] = useState(() => {
    return localStorage.getItem('mikroman_lang') || 'en';
  });

  useEffect(() => {
    localStorage.setItem('mikroman_lang', lang);
  }, [lang]);

  const t = (key, params = {}) => {
    const langDict = translations[lang] || translations.en;
    let text = langDict[key] || translations.en[key] || key;
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
      });
    }
    return text;
  };

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  );
};

export const useI18n = () => useContext(I18nContext);
