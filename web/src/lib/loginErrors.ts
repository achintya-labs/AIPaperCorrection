const MESSAGES: Record<string, string> = {
  google_not_allowed: "That Google account has no access. Ask an admin to add your email, or sign in with a password.",
  google_cancelled: "Google sign-in was cancelled.",
  google_failed: "Google sign-in failed. Please try again.",
};

/** Message for the ?error= code the API redirects to /login with; unknown codes show nothing. */
export function loginErrorMessage(code: string | null): string {
  return (code && MESSAGES[code]) || "";
}
