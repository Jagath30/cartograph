"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "@/lib/api";
import { whereTo, type Session } from "@/lib/auth/next";

/**
 * Who is signed in, for every page (step 11). The session itself is the
 * HttpOnly cookie, which no script can read: this asks the API who it is,
 * once when the frame loads and again when told to (after login, logout,
 * or a 401 on the way).
 */

type SessionContext = {
  session: Session;
  /** Ask the API again who the session is. */
  refresh: () => Promise<void>;
  /** A call came back 401: the session has ended. */
  lost: () => void;
  signOut: () => Promise<void>;
};

const Context = createContext<SessionContext | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>({ kind: "checking" });

  const refresh = useCallback(async () => {
    try {
      const account = await api.me();
      setSession({ kind: "in", email: account.email });
    } catch {
      // A 401, or no answer at all: either way no one is known to be
      // signed in, and a page that needs a session sends the visitor to
      // sign in.
      setSession({ kind: "out" });
    }
  }, []);

  // The first check, once the page is in the browser.
  useEffect(() => {
    let current = true;
    api
      .me()
      .then((account) => current && setSession({ kind: "in", email: account.email }))
      .catch(() => current && setSession({ kind: "out" }));
    return () => {
      current = false;
    };
  }, []);

  const lost = useCallback(() => setSession({ kind: "out" }), []);
  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setSession({ kind: "out" });
    }
  }, []);

  const value = useMemo(() => ({ session, refresh, lost, signOut }), [session, refresh, lost, signOut]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSession(): SessionContext {
  const context = useContext(Context);
  if (context === null) throw new Error("useSession is used outside the SessionProvider in app/layout.tsx");
  return context;
}

/**
 * For a page that needs a session: while it is checked, nothing; signed
 * in, the account; signed out, to /login and back again. A 401 from any
 * later call goes to `lost`, which ends here too.
 */
export function useRequiredSession(): { email: string | null; show: boolean; lost: () => void } {
  const { session, lost } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const decided = whereTo(session, pathname, typeof window === "undefined" ? "" : window.location.search);
  useEffect(() => {
    if (decided.redirect) router.replace(decided.redirect);
  }, [decided.redirect, router]);
  return { email: session.kind === "in" ? session.email : null, show: decided.show, lost };
}

const LINK = "rounded-sm underline-offset-4 hover:underline";

/** The header's account: what it shows for each state of the session. */
export function AccountView({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  if (session.kind === "checking") return null;
  if (session.kind === "out") {
    return (
      <Link href="/login" className={`text-sm text-gray-700 ${LINK}`}>
        Sign in
      </Link>
    );
  }
  return (
    <span className="flex items-center gap-x-4 text-sm text-gray-700">
      <Link href="/history" className={LINK}>
        History
      </Link>
      <span className="max-w-[16rem] truncate" title={`Signed in as ${session.email}`}>
        {session.email}
      </span>
      <button type="button" onClick={onSignOut} className={`text-gray-700 ${LINK}`}>
        Sign out
      </button>
    </span>
  );
}

export function Account() {
  const { session, signOut } = useSession();
  const router = useRouter();
  const out = useCallback(() => {
    void signOut().then(() => router.push("/login"));
  }, [signOut, router]);
  return <AccountView session={session} onSignOut={out} />;
}
