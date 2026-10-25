export type AccountState =
  | "unconfigured"
  | "connecting"
  | "offline"
  | "logged_out"
  | "code_sent"
  | "password_needed"
  | "ready";

export interface AccountStatus {
  state: AccountState;
  phone: string | null;
  me: { name: string; username: string | null; phone: string | null } | null;
}
