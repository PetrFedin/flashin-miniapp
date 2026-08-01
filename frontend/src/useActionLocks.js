import { useRef, useState } from "react";

import { createActionCoordinator } from "./actionCoordinator.js";

export function useActionLocks() {
  const [busyKeys, setBusyKeys] = useState(() => new Set());
  const coordinatorRef = useRef(null);

  if (!coordinatorRef.current) {
    coordinatorRef.current = createActionCoordinator((keys) => {
      setBusyKeys(new Set(keys));
    });
  }

  return {
    runAction: coordinatorRef.current.run,
    isBusy(key) {
      return busyKeys.has(String(key || "").trim());
    },
    hasBusyActions: busyKeys.size > 0,
  };
}
