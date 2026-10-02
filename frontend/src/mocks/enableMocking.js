export async function enableMocking() {
  if (!import.meta.env.DEV || import.meta.env.VITE_MSW_ENABLED !== "true") {
    return false;
  }
  const scenario = String(import.meta.env.VITE_MSW_SCENARIO || "").trim();
  if (!scenario) return false;
  const { createScenarioWorker } = await import("./browser.js");
  const worker = createScenarioWorker(scenario);
  await worker.start({
    onUnhandledRequest: "bypass",
    quiet: true,
  });
  return true;
}
