import { useCallback, useEffect, useRef, useState } from "react";
import { classifyApiError, retryDelayMs, shouldClearData } from "@/lib/apiError";
import { createRequestGate } from "@/lib/requestGate";

/**
 * One loading/error/data lifecycle for every page.
 *
 * It exists because seven pages each hand-rolled the same three lines, and all
 * seven got them wrong in the same two ways: `.catch(() => setRows([]))` turned
 * every failure into a convincing empty state, and three pages had no `.catch`
 * at all, so a rejection left the skeleton on screen permanently.
 *
 * The interesting logic lives in lib/apiError.js and lib/requestGate.js, which
 * are plain functions with tests. This is the React seam around them.
 *
 * @param {() => Promise<any>} fetcher
 * @param {object}   [options]
 * @param {any[]}    [options.deps]      re-fetch when these change
 * @param {string}   [options.resetKey]  identity of the scope (e.g. workspace id).
 *                                       A change clears data before re-fetching,
 *                                       so the previous scope's rows never show
 *                                       under the new one.
 * @param {number}   [options.maxAutoRetries]
 */
export function useApiResource(fetcher, options = {}) {
    const { deps = [], resetKey = null, maxAutoRetries = 2 } = options;

    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [retryAt, setRetryAt] = useState(null);

    const gate = useRef(null);
    if (gate.current === null) gate.current = createRequestGate();

    const fetcherRef = useRef(fetcher);
    fetcherRef.current = fetcher;

    const attempts = useRef(0);
    const timer = useRef(null);
    const previousKey = useRef(resetKey);

    const run = useCallback(() => {
        const token = gate.current.begin();
        setLoading(true);
        setError(null);

        Promise.resolve()
            .then(() => fetcherRef.current())
            .then((result) => {
                // A superseded or closed request must not touch state. This is
                // what stops a slow response from a previous workspace from
                // repopulating the screen.
                if (!gate.current.accept(token)) return;
                attempts.current = 0;
                setData(result);
                setError(null);
                setLoading(false);
                setRetryAt(null);
            })
            .catch((err) => {
                if (!gate.current.accept(token)) return;
                const classified = classifyApiError(err);

                // Losing access clears what is on screen. Leaving a stale list
                // visible under a permission error is worse than showing none.
                if (shouldClearData(classified.kind)) setData(null);

                // Bounded automatic retry, and only for conditions where
                // retrying can actually succeed. 401/403/404 are never retried:
                // the same request would produce the same answer.
                if (classified.retryable && attempts.current < maxAutoRetries) {
                    const delay = retryDelayMs(classified, attempts.current);
                    attempts.current += 1;
                    setError(classified);
                    setRetryAt(Date.now() + delay);
                    timer.current = setTimeout(() => {
                        if (!gate.current.isClosed) run();
                    }, delay);
                    return;
                }

                setError(classified);
                // Always cleared, whatever the outcome. A rejected request can
                // never leave a permanent skeleton.
                setLoading(false);
                setRetryAt(null);
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [maxAutoRetries]);

    useEffect(() => {
        // Scope changed: drop the old scope's data before anything new lands,
        // and invalidate whatever is already in flight for the old scope.
        if (previousKey.current !== resetKey) {
            previousKey.current = resetKey;
            gate.current.invalidate();
            setData(null);
            setError(null);
        }
        attempts.current = 0;
        run();

        return () => {
            if (timer.current) clearTimeout(timer.current);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [run, resetKey, ...deps]);

    useEffect(() => {
        const g = gate.current;
        return () => {
            g.close();
            if (timer.current) clearTimeout(timer.current);
        };
    }, []);

    const retry = useCallback(() => {
        attempts.current = 0;
        if (timer.current) clearTimeout(timer.current);
        run();
    }, [run]);

    return { data, error, loading, retry, retryAt };
}

export default useApiResource;
