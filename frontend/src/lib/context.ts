import { createContext, useContext } from "react";
import type { AccountStatus } from "./account";

export interface AccountCtx {
  account: AccountStatus;
  setAccount: (s: AccountStatus) => void;
  reloadAccount: () => Promise<void>;
}

export const AccountContext = createContext<AccountCtx | null>(null);

export function useAccount(): AccountCtx {
  const ctx = useContext(AccountContext);
  if (!ctx) throw new Error("AccountContext missing");
  return ctx;
}
