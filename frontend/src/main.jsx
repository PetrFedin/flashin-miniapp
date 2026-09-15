import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import PreviewApp from './PreviewApp';
import './styles.css';

const RootApp = import.meta.env.VITE_BROWSER_PREVIEW === 'true' ? PreviewApp : App;

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RootApp />
  </React.StrictMode>
);
