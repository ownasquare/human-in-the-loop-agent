import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ApiError } from "./api";
import { App } from "./App";
import { LiveRegionProvider } from "./components/LiveRegion";
import { ThemeProvider } from "./components/ThemeProvider";
import "./styles/app.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (attempt, error) => {
        if (error instanceof ApiError && (error.status < 500 || error.code === "request_cancelled")) return false;
        return attempt < 1;
      },
      refetchOnWindowFocus: true,
      staleTime: 5_000,
    },
    mutations: { retry: false },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <LiveRegionProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </LiveRegionProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
