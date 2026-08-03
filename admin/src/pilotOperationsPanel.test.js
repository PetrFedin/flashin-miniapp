import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panelSource = readFileSync(
  new URL("./PilotOperationsPanel.jsx", import.meta.url),
  "utf8",
);
const wrapperSource = readFileSync(
  new URL("./BusinessEventsPanel.jsx", import.meta.url),
  "utf8",
);

test("pilot admin panel is wired to the protected read-only status endpoint", () => {
  assert.match(panelSource, /adminJson\("\/api\/ops\/pilot-runtime"/);
  assert.match(panelSource, /Cache-Control": "no-cache"/);
  assert.match(panelSource, /REFRESH_INTERVAL_MS = 30_000/);
  assert.match(panelSource, /document\.visibilityState !== "hidden"/);
});

test("pilot admin panel contains no runtime or money mutation controls", () => {
  const forbidden = [
    /method:\s*"POST"/,
    /method:\s*"PUT"/,
    /method:\s*"PATCH"/,
    /method:\s*"DELETE"/,
    /pilot-runtime\/arm/,
    /pilot-runtime\/resume/,
    /pilot-runtime\/stop/,
    /pilot-runtime\/reset/,
    /allowed_telegram_ids/,
    /provider_payment_id/,
    /provider_refund_id/,
  ];
  for (const pattern of forbidden) {
    assert.doesNotMatch(panelSource, pattern);
  }
});

test("pilot status is rendered before BusinessEvent recovery", () => {
  const pilotPosition = wrapperSource.indexOf("<PilotOperationsPanel");
  const recoveryPosition = wrapperSource.indexOf("<BusinessEventsRecoveryPanel");

  assert.ok(pilotPosition >= 0, "PilotOperationsPanel is not rendered");
  assert.ok(recoveryPosition >= 0, "BusinessEventsRecoveryPanel is not rendered");
  assert.ok(pilotPosition < recoveryPosition, "Pilot status must precede recovery controls");
});
