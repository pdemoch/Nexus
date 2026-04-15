import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'
import axios from 'axios'

// Descobre automaticamente se estamos no servidor ou no PC local
const isProd = import.meta.env.PROD;

// Configura a URL base
axios.defaults.baseURL = isProd 
  ? 'https://api.lineanexus.com.br' 
  : 'http://localhost:8000';

// INTERCEPTOR DE SEGURANÇA: Anexa o Crachá (Token) em TODOS os pedidos!
axios.interceptors.request.use((config) => {
  const token = localStorage.getItem('nexus_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}, (error) => {
  return Promise.reject(error);
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)