import { useEffect, useState } from "react";

const THEME_VARIABLES = {
  bg_color: "--tg-bg_color",
  text_color: "--tg-text_color",
  hint_color: "--tg-hint_color",
  link_color: "--tg-link_color",
  button_color: "--tg-button_color",
  button_text_color: "--tg-button_text_color",
  secondary_bg_color: "--tg-secondary_bg_color",
  header_bg_color: "--tg-header_bg_color",
  accent_text_color: "--tg-accent_text_color",
  section_bg_color: "--tg-section_bg_color",
  section_header_text_color: "--tg-section_header_text_color",
  subtitle_text_color: "--tg-subtitle_text_color",
  destructive_text_color: "--tg-destructive_text_color",
};

function setCssNumber(name, value) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return;
  document.documentElement.style.setProperty(name, `${Number(value)}px`);
}

function applyTheme(webApp) {
  const root = document.documentElement;
  if (!root) return;

  Object.entries(webApp.themeParams || {}).forEach(([key, value]) => {
    root.style.setProperty(THEME_VARIABLES[key] || `--tg-${key}`, value);
  });

  root.dataset.tgColorScheme = webApp.colorScheme || "light";
  root.dataset.tgPlatform = webApp.platform || "unknown";
  root.style.colorScheme = webApp.colorScheme || "light";
}

function applyViewport(webApp) {
  setCssNumber("--tg-viewport-height", webApp.viewportHeight || window.innerHeight);
  setCssNumber("--tg-viewport-stable-height", webApp.viewportStableHeight || webApp.viewportHeight || window.innerHeight);

  const safeArea = webApp.safeAreaInset || {};
  const contentSafeArea = webApp.contentSafeAreaInset || {};
  setCssNumber("--tg-safe-area-top", safeArea.top || 0);
  setCssNumber("--tg-safe-area-right", safeArea.right || 0);
  setCssNumber("--tg-safe-area-bottom", safeArea.bottom || 0);
  setCssNumber("--tg-safe-area-left", safeArea.left || 0);
  setCssNumber("--tg-content-safe-area-top", contentSafeArea.top || 0);
  setCssNumber("--tg-content-safe-area-right", contentSafeArea.right || 0);
  setCssNumber("--tg-content-safe-area-bottom", contentSafeArea.bottom || 0);
  setCssNumber("--tg-content-safe-area-left", contentSafeArea.left || 0);
}

export function useTelegram() {
  const [tg, setTg] = useState(null);
  const [user, setUser] = useState(null);
  const [initData, setInitData] = useState("");
  const [launchContext, setLaunchContext] = useState({
    platform: "unknown",
    version: "0.0",
    colorScheme: "light",
    startParam: "",
  });

  useEffect(() => {
    const webApp = window?.Telegram?.WebApp;
    if (!webApp) return;

    setTg(webApp);
    setUser(webApp.initDataUnsafe?.user || null);
    setInitData(webApp.initData || "");
    setLaunchContext({
      platform: webApp.platform || "unknown",
      version: webApp.version || "0.0",
      colorScheme: webApp.colorScheme || "light",
      startParam: webApp.initDataUnsafe?.start_param || "",
    });

    try {
      webApp.ready();
      webApp.expand();
      webApp.setHeaderColor?.("bg_color");
      webApp.setBackgroundColor?.("bg_color");
      webApp.setBottomBarColor?.("bottom_bar_bg_color");
      webApp.disableVerticalSwipes?.();
    } catch (err) {
      console.error("Telegram WebApp init failed", err);
    }

    const refreshTheme = () => {
      applyTheme(webApp);
      setLaunchContext((current) => ({
        ...current,
        colorScheme: webApp.colorScheme || current.colorScheme,
      }));
    };
    const refreshViewport = () => applyViewport(webApp);

    refreshTheme();
    refreshViewport();

    webApp.onEvent?.("themeChanged", refreshTheme);
    webApp.onEvent?.("viewportChanged", refreshViewport);
    webApp.onEvent?.("safeAreaChanged", refreshViewport);
    webApp.onEvent?.("contentSafeAreaChanged", refreshViewport);
    window.addEventListener("resize", refreshViewport);

    return () => {
      webApp.offEvent?.("themeChanged", refreshTheme);
      webApp.offEvent?.("viewportChanged", refreshViewport);
      webApp.offEvent?.("safeAreaChanged", refreshViewport);
      webApp.offEvent?.("contentSafeAreaChanged", refreshViewport);
      window.removeEventListener("resize", refreshViewport);
    };
  }, []);

  return { tg, user, initData, launchContext };
}
