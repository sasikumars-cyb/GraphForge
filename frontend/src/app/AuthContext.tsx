import { useEffect, useState, type ReactNode } from "react";
import { fetchCurrentUser, login as apiLogin } from "../lib/api/auth";
import { UNAUTHORIZED_EVENT } from "../lib/api/client";
import type { User } from "../types/auth";
import { AuthContext } from "./auth-context";

const TOKEN_STORAGE_KEY = "graphforge.token";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_STORAGE_KEY));
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!token) {
      setUser(null);
      setIsLoading(false);
      return;
    }

    let cancelled = false;
    setIsLoading(true);

    fetchCurrentUser(token)
      .then((currentUser) => {
        if (!cancelled) setUser(currentUser);
      })
      .catch(() => {
        // Token is invalid or expired - drop it rather than loop forever.
        if (!cancelled) {
          localStorage.removeItem(TOKEN_STORAGE_KEY);
          setToken(null);
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  // A token can die mid-session (expiry, or the account being deactivated)
  // with no fetch of this component's own to notice — every other page's
  // API calls just started failing with a generic error banner and no way
  // back to /login short of a manual browser refresh. apiFetch dispatches
  // this event specifically (and only) when the backend reports the
  // bearer token itself is invalid (see UNAUTHORIZED_EVENT's docstring),
  // so this can log out immediately without waiting for a future mount.
  // setToken/setUser are stable across renders, so an empty dependency
  // array is safe here — no stale-closure risk.
  useEffect(() => {
    function handleInvalidToken() {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
      setToken(null);
      setUser(null);
    }
    window.addEventListener(UNAUTHORIZED_EVENT, handleInvalidToken);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handleInvalidToken);
  }, []);

  async function login(email: string, password: string) {
    const tokens = await apiLogin(email, password);
    localStorage.setItem(TOKEN_STORAGE_KEY, tokens.access_token);
    setToken(tokens.access_token);
  }

  function logout() {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    setToken(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, token, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
