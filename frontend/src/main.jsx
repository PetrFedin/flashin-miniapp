import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import CatalogExperience from "./CatalogExperience";
import ProductIntentExperience from "./ProductIntentExperience";
import SharedProductLanding from "./SharedProductLanding";
import { enableMocking } from "./mocks/enableMocking.js";
import { initFrontendObservability } from "./observability.js";
import { createFlashinQueryClient } from "./queryClient.js";
import "./styles.css";
import "./catalog-plus.css";
import "./catalog-intents.css";

const queryClient = createFlashinQueryClient();
initFrontendObservability("mini-app");

async function mount() {
  await enableMocking();
  createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <>
          <App />
          <CatalogExperience />
          <ProductIntentExperience />
          <SharedProductLanding />
        </>
      </QueryClientProvider>
    </React.StrictMode>,
  );
}

void mount();
