import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../context/AuthContext";
import { invalidateWorkspaceQueries, shouldRefreshWorkspaceForPush } from "../lib/workspaceSync";

const WS_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/^http/, "ws");

/**
 * FEATURE 17: subscribes to /ws/companies/{id} and invalidates the relevant
 * React Query caches on push, so chat replies, task moves, and events show
 * up live without polling. Reconnects with backoff if the socket drops.
 */
export function useCompanyWebSocket(companyId) {
  const { auth } = useAuth();
  const queryClient = useQueryClient();
  const retryRef = useRef(0);

  useEffect(() => {
    if (!companyId || !auth?.token) return undefined;

    let socket;
    let closedByUs = false;
    let retryTimeout;
    let refreshTimeout;

    const connect = () => {
      socket = new WebSocket(`${WS_BASE}/ws/companies/${companyId}?token=${auth.token}`);

      socket.onopen = () => {
        retryRef.current = 0;
      };

      socket.onerror = () => {
        // Browsers intentionally hide handshake details; include only the
        // company scope for local diagnostics, never the query-string token.
        console.warn("Company WebSocket connection failed", { companyId });
      };

      socket.onmessage = (event) => {
        try {
          const { type } = JSON.parse(event.data);
          if (shouldRefreshWorkspaceForPush(type)) {
            // Some server events are broadcast inside the transaction that
            // writes them. Coalesce pushes briefly so REST reads see committed
            // state, and collapse duplicate broadcasts into one refresh.
            clearTimeout(refreshTimeout);
            refreshTimeout = setTimeout(() => {
              invalidateWorkspaceQueries(queryClient);
            }, 150);
          }
        } catch {
          // ignore malformed frames
        }
      };

      socket.onclose = (event) => {
        if (import.meta.env.DEV) {
          console.info("Company WebSocket closed", { companyId, code: event.code, reason: event.reason });
        }
        if (closedByUs) return;
        // A rejected ownership/token handshake should not spin forever. A
        // successful connection resets this counter and restores retries.
        if (retryRef.current >= 8) return;
        const delay = Math.min(1000 * 2 ** retryRef.current, 15000);
        retryRef.current += 1;
        retryTimeout = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      closedByUs = true;
      clearTimeout(retryTimeout);
      clearTimeout(refreshTimeout);
      socket?.close();
    };
  }, [companyId, auth?.token, queryClient]);
}
