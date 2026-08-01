export function createActionCoordinator(onChange = () => {}) {
  const inFlight = new Map();

  function notify() {
    onChange([...inFlight.keys()]);
  }

  function run(key, operation) {
    const normalizedKey = String(key || "").trim();
    if (!normalizedKey) throw new Error("Action key is required");
    if (typeof operation !== "function") throw new TypeError("Action operation must be a function");

    const existing = inFlight.get(normalizedKey);
    if (existing) return existing;

    const promise = Promise.resolve()
      .then(operation)
      .finally(() => {
        if (inFlight.get(normalizedKey) === promise) {
          inFlight.delete(normalizedKey);
          notify();
        }
      });

    inFlight.set(normalizedKey, promise);
    notify();
    return promise;
  }

  return {
    run,
    isBusy(key) {
      return inFlight.has(String(key || "").trim());
    },
    keys() {
      return [...inFlight.keys()];
    },
  };
}
