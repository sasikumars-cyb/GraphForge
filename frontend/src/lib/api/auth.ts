import { apiFetch } from "./client";
import type { AuthTokens, User } from "../../types/auth";

export function login(email: string, password: string): Promise<AuthTokens> {
  return apiFetch<AuthTokens>("/auth/login", {
    method: "POST",
    body: { email, password },
  });
}

export function fetchCurrentUser(token: string): Promise<User> {
  return apiFetch<User>("/auth/me", { token });
}
