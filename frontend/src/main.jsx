import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import CatalogExperience from './CatalogExperience';
import './styles.css';
import './catalog-plus.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <>
      <App />
      <CatalogExperience />
    </>
  </React.StrictMode>
);
