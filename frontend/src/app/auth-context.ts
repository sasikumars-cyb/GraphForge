import { createContext, useContext } from "react";
import type { User } from "../types/auth";

export interface AuthContextValue {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** Adopts a token this app already issued elsewhere (KAN-34's GitHub
   * OAuth login redirect carries one in `/oauth/callback?token=`) rather
   * than obtaining one via `login`'s own email/password POST. */
  loginWithToken: (token: string) => void;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
