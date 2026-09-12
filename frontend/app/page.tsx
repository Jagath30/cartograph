"use client";

import { useEffect, useState } from "react";

import { api, ApiError, type Readiness } from "@/lib/api";

export default function Home() {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .ready()
      .then(setReadiness)
      .catch((cause: unknown) =>
        setError(
          cause instanceof ApiError
            ? `${cause.status} ${cause.code}: ${cause.message}`
            : String(cause),
        ),
      );
  }, []);

  return (
    <main className="mx-auto max-w-xl p-10 font-sans">
      <h1 className="text-2xl font-semibold">Cartograph</h1>
      <p className="mt-2 text-sm text-gray-500">
        Step 1 &mdash; the stack stands up empty.
      </p>

      <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-gray-500">
        Backend readiness
      </h2>

      {error && (
        <p className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      )}

      {!error && !readiness && (
        <p className="mt-3 text-sm text-gray-500">checking&hellip;</p>
      )}

      {readiness && (
        <ul className="mt-3 space-y-1">
          {Object.entries(readiness.checks).map(([name, check]) => (
            <li key={name} className="flex justify-between border-b py-2 text-sm">
              <span className="font-mono">{name}</span>
              <span className={check.ok ? "text-green-700" : "text-red-700"}>
                {check.ok ? "ok" : check.detail}
              </span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
