import { useEffect } from 'react';
import axios from 'axios';

/**
 * Hook que mantém a sessão "viva" no banco de dados.
 * Ele avisa o backend a cada 30 segundos que o usuário está com a tela aberta.
 */
export function useHeartbeat(token: string | null) {
  useEffect(() => {
    if (!token) return;

    const enviarPulso = async () => {
      try {
        // Envia o ping silencioso para o backend
        await axios.post('/api/v1/auth/heartbeat', {}, {
          headers: { Authorization: `Bearer ${token}` }
        });
      } catch (error) {
        // Falhas silenciosas (se a internet cair, o pulso simplesmente para)
      }
    };

    // Envia o primeiro pulso imediatamente ao carregar a tela
    enviarPulso();

    // Configura o intervalo para bater a cada 30 segundos
    const interval = setInterval(enviarPulso, 30000);

    // Limpa o intervalo se o usuário fechar a tela ou fizer logout
    return () => clearInterval(interval);
  }, [token]);
}