"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { WAKING, watchWaking } from "@/lib/waking";

/** The notice itself: a polite status, nothing at all when not shown. */
export function WakingView({ shown }: { shown: boolean }) {
  if (!shown) return null;
  return (
    <div role="status" className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900">
      {WAKING}
    </div>
  );
}

/** On every page: asks the health check once the page loads, and says the
 *  server is waking when it has not answered within 3 seconds (step 12). */
export function WakingNotice() {
  const [shown, setShown] = useState(false);
  useEffect(() => watchWaking(api.health, setShown), []);
  return <WakingView shown={shown} />;
}
