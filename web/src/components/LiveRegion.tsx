/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

interface LiveRegionValue {
  announce: (message: string) => void;
}

const LiveRegionContext = createContext<LiveRegionValue | null>(null);

export function LiveRegionProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  const announce = useCallback((next: string) => {
    setMessage("");
    window.setTimeout(() => setMessage(next), 20);
  }, []);
  const value = useMemo(() => ({ announce }), [announce]);

  return (
    <LiveRegionContext.Provider value={value}>
      {children}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {message}
      </div>
    </LiveRegionContext.Provider>
  );
}

export function useLiveRegion(): LiveRegionValue {
  const context = useContext(LiveRegionContext);
  if (!context) throw new Error("useLiveRegion must be used inside LiveRegionProvider");
  return context;
}
