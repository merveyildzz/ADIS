import { useEffect } from "react";

/** Clears `value` via `onClear` after `delayMs` — used so transient error
 * banners (an upload rejection, a client-side validation message) don't sit
 * on screen forever; the user can still act on it immediately, it just
 * doesn't linger once it's no longer relevant. */
export default function useAutoDismiss(value, onClear, delayMs = 4000) {
  useEffect(() => {
    if (!value) return;
    const timer = setTimeout(onClear, delayMs);
    return () => clearTimeout(timer);
  }, [value, onClear, delayMs]);
}
