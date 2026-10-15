import React from 'react';
import ReactDOM from 'react-dom/client';

import App from './App.jsx';
import './styles.css';
import './chat-first.css';
import './chat-layout.css';
import './compact-pages.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
