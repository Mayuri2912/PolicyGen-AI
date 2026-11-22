import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import App from './App.jsx';
import { SystemProvider } from './context/SystemContext.jsx';
import { ToastProvider } from './context/ToastContext.jsx';
import './styles.css';

const Router = import.meta.env.VITE_ROUTER_MODE === 'hash' ? HashRouter : BrowserRouter;

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Router>
      <ToastProvider>
        <SystemProvider>
          <App />
        </SystemProvider>
      </ToastProvider>
    </Router>
  </React.StrictMode>,
);
