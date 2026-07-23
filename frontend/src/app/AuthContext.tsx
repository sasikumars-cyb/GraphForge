import { useEffect, useState, type ReactNode } from "react";
import { fetchCurrentUser, login as apiLogin } from "../lib/api/auth";
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
