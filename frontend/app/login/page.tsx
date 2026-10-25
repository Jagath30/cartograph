"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { FIELD, SUBMIT, said } from "@/components/account/form";
import { WIDTH } from "@/components/frame/Frame";
import { useSession } from "@/components/frame/Session";
import { api } from "@/lib/api";
import { safeNext } from "@/lib/auth/next";

function SignIn() {
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
      .login(email, password)
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
          autoComplete="current-password"
          required
          maxLength={128}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className={FIELD}
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-red-800">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy} className={SUBMIT}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <p className="text-sm text-gray-700">
        No account?{" "}
        <Link href={`/register?next=${encodeURIComponent(destination)}`} className="underline underline-offset-4">
          Create one
        </Link>
        .
      </p>
    </form>
  );
}

export default function Login() {
  return (
    <div className={`${WIDTH} py-6 font-sans`}>
      <h1 className="mb-4 text-xl font-semibold">Sign in</h1>
      {/* useSearchParams needs a boundary for the production build. */}
      <Suspense fallback={<p className="text-sm text-gray-500">&hellip;</p>}>
        <SignIn />
      </Suspense>
    </div>
  );
}
