import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import CatalogExperience from './CatalogExperience';
import ProductIntentExperience from './ProductIntentExperience';
import SharedProductLanding from './SharedProductLanding';
import './styles.css';
import './catalog-plus.css';
import './catalog-intents.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <>
      <App />
      <CatalogExperience />
      <ProductIntentExperience />
      <SharedProductLanding />
    </>
  </React.StrictMode>
);
