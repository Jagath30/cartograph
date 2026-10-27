import { ApiError } from "./api";

/**
 * The cold start (step 12, item 85). The free host sleeps the backend
 * after 15 minutes without visitors and takes about a minute to wake.
 * When the health check (api.health) has not answered within 3 seconds the page says so;
 * the notice goes when the server answers. An error the server sent is an
 * answer: it is awake. No answer at all (status 0) is not: the notice
 * stays and health is asked again.
 */
export const WAKING =
  "Waking the server. Free hosting sleeps after 15 minutes without visitors; the first request can take up to a minute.";
export const WAKING_AFTER_MS = 3000;
export const ASK_AGAIN_MS = 5000;

/** Starts watching; returns the function that stops it. */
export function watchWaking(health: () => Promise<unknown>, show: (shown: boolean) => void): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined = setTimeout(() => {
    if (!stopped) show(true);
  }, WAKING_AFTER_MS);

  const answered = () => {
    clearTimeout(timer);
    if (!stopped) show(false);
  };

  const ask = () => {
    health().then(answered, (error: unknown) => {
      if (error instanceof ApiError && error.status !== 0) {
        answered();
        return;
      }
      if (!stopped) timer = setTimeout(() => {
        if (stopped) return;
        show(true);
        ask();
      }, ASK_AGAIN_MS);
    });
  };
  ask();

  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
