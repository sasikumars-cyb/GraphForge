/**
 * Types for the real (non-mock) auth API — mirrors backend/app/schemas/auth.py.
 */

export interface User {
  id: string;
  email: string;
  full_name: string;
  auth_provider: string;
  role: string;
  created_at: string;
}

export interface AuthTokens {
  access_token: string;
  token_type: string;
}
