import { useEffect, useState } from "react";

export function useTelegram() {
  const [tg, setTg] = useState(null);
  const [user, setUser] = useState(null);
  const [initData, setInitData] = useState("");

  useEffect(() => {
    const webApp = window?.Telegram?.WebApp;
    if (!webApp) return;

    setTg(webApp);
    setUser(webApp.initDataUnsafe?.user || null);
    setInitData(webApp.initData || "");

    try {
      webApp.ready();
      webApp.expand();
    } catch (err) {
      console.error("Telegram WebApp init failed", err);
    }

    const applyTheme = (theme) => {
      const root = document.documentElement;
      if (!theme || !root) return;
      Object.keys(theme).forEach((key) => root.style.setProperty(`--tg-${key}`, theme[key]));
    };
    applyTheme(webApp.themeParams);

    const onThemeChanged = () => applyTheme(webApp.themeParams);
    webApp.onEvent("themeChanged", onThemeChanged);
    return () => webApp.offEvent("themeChanged", onThemeChanged);
  }, []);

  return { tg, user, initData };
}
