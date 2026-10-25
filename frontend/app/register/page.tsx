"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { FIELD, SUBMIT, said } from "@/components/account/form";
import { WIDTH } from "@/components/frame/Frame";
import { useSession } from "@/components/frame/Session";
import { api } from "@/lib/api";
import { safeNext } from "@/lib/auth/next";

/** An account, then a session: registering does not sign in, so this
 *  logs in straight after. No email is sent; there is no verification
 *  and no reset (out of scope, step 11). */
function Register() {
  const router = useRouter();
  const { refresh } = useSession();
  // Where to go back to: only a path on this site, else /ask.
  const destination = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    api
      .register(email, password)
      .then(() => api.login(email, password))
      .then(refresh)
      .then(() => router.replace(destination))
      .catch((cause: unknown) => {
        setError(said(cause));
        setBusy(false);
      });
  };

  return (
    <form onSubmit={submit} className="max-w-sm space-y-4">
      <label className="block text-sm font-medium text-gray-800">
        Email
        <input
          type="email"
          name="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className={FIELD}
        />
      </label>
      <label className="block text-sm font-medium text-gray-800">
        Password
        <input
          type="password"
          name="password"
          autoComplete="new-password"
          required
          minLength={8}
          maxLength={128}
          aria-describedby="password-rule"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className={FIELD}
        />
        <span id="password-rule" className="mt-1 block text-xs font-normal text-gray-600">
          8 to 128 characters.
        </span>
      </label>
      {error && (
        <p role="alert" className="text-sm text-red-800">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy} className={SUBMIT}>
        {busy ? "Creating the account…" : "Create the account"}
      </button>
      <p className="text-sm text-gray-700">
        Already have one?{" "}
        <Link href={`/login?next=${encodeURIComponent(destination)}`} className="underline underline-offset-4">
          Sign in
        </Link>
        .
      </p>
    </form>
  );
}

export default function RegisterPage() {
  return (
    <div className={`${WIDTH} py-6 font-sans`}>
      <h1 className="mb-4 text-xl font-semibold">Create an account</h1>
      {/* useSearchParams needs a boundary for the production build. */}
      <Suspense fallback={<p className="text-sm text-gray-500">&hellip;</p>}>
        <Register />
      </Suspense>
    </div>
  );
}
