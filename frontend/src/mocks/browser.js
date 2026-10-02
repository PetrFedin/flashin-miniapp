import { setupWorker } from "msw/browser";
import { handlersForScenario } from "./handlers.js";

export function createScenarioWorker(scenario) {
  return setupWorker(...handlersForScenario(scenario));
}
