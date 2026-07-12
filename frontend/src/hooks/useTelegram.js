import { useEffect, useMemo, useState } from "react";

function applyTheme(themeParams) {
  const root = document?.documentElement;
  if (!root || !themeParams) return;

  Object.entries(themeParams).forEach(([key, value]) => {
    if (typeof value === "string" && value) {
      root.style.setProperty(`--tg-${key}`, value);
    }
  });
}

export function useTelegram() {
  const [tg, setTg] = useState(null);
  const [user, setUser] = useState(null);
  const [initData, setInitData] = useState("");
  const [status, setStatus] = useState("initializing");
  const [error, setError] = useState("");

  useEffect(() => {
    if (typeof window === "undefined") {
      setStatus("browser");
      return undefined;
    }

    const webApp = window.Telegram?.WebApp;
    if (!webApp) {
      setStatus("browser");
      return undefined;
    }

    try {
      setTg(webApp);
      setUser(webApp.initDataUnsafe?.user || null);
      setInitData(webApp.initData || "");
      applyTheme(webApp.themeParams);

      webApp.ready?.();
      webApp.expand?.();
      webApp.enableClosingConfirmation?.();

      setStatus(webApp.initData ? "ready" : "missing_init_data");
      setError("");
    } catch (err) {
      console.error("Telegram WebApp initialization failed", err);
      setStatus("error");
      setError("Не удалось инициализировать Telegram Mini App.");
    }

    const onThemeChanged = () => applyTheme(webApp.themeParams);
    webApp.onEvent?.("themeChanged", onThemeChanged);

    return () => {
      webApp.offEvent?.("themeChanged", onThemeChanged);
    };
  }, []);

  const isTelegram = Boolean(tg);
  const canAuthenticate = isTelegram && Boolean(initData);

  return useMemo(
    () => ({
      tg,
      user,
      initData,
      status,
      error,
      isTelegram,
      canAuthenticate,
    }),
    [tg, user, initData, status, error, isTelegram, canAuthenticate],
  );
}
