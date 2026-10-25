export type AccountState =
  | "unconfigured"
  | "connecting"
  | "offline"
  | "logged_out"
  | "code_sent"
  | "qr_waiting"
  | "password_needed"
  | "ready";

export type CodeVia = "app" | "sms" | "call" | "email" | "fragment" | "setup_email" | "other";

export interface AccountStatus {
  state: AccountState;
  phone: string | null;
  /** Where Telegram delivered the login code (only while state is code_sent). */
  code: {
    via: CodeVia;
    next_via: CodeVia | null;
    resend_in: number;
    email_pattern: string | null;
    fragment_url: string | null;
  } | null;
  qr: { url: string; svg: string } | null;
  login_error: string | null;
  me: { name: string; username: string | null; phone: string | null } | null;
}
