/**
 * Sliding-window rate limiter for authenticated HTTP API requests.
 *
 * Tracks requests per client (by IP or token hash) and enforces a configurable
 * maximum number of requests within a rolling time window. Designed as an
 * opt-in security hardening measure (OC-SEC-028) to protect API endpoints
 * (`/v1/chat/completions`, `/v1/responses`, `/tools/invoke`) from abuse.
 *
 * Design decisions:
 * - Pure in-memory Map (no external deps); suitable for single-process gateway.
 * - Sliding window: each request timestamp is stored; expired entries are pruned
 *   lazily on check + periodically via a background timer.
 * - Loopback addresses (127.0.0.1 / ::1) are exempt by default so local CLI
 *   sessions are never throttled.
 * - Disabled by default (opt-in) to avoid breaking existing deployments.
 */

import type { IncomingMessage, ServerResponse } from "node:http";
import { sendJson } from "./http-common.js";
import { isLoopbackAddress, resolveClientIp } from "./net.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface HttpRateLimitConfig {
  /** Maximum requests allowed within the window. @default 60 */
  maxRequests?: number;
  /** Sliding window duration in milliseconds. @default 60_000 (1 min) */
  windowMs?: number;
  /** Exempt loopback (localhost) addresses from rate limiting. @default true */
  exemptLoopback?: boolean;
  /** Background prune interval in milliseconds; set <= 0 to disable. @default 60_000 */
  pruneIntervalMs?: number;
}

export interface HttpRateLimitCheckResult {
  /** Whether the request is allowed to proceed. */
  allowed: boolean;
  /** Number of remaining requests in the current window. */
  remaining: number;
  /** Milliseconds until the oldest tracked request exits the window (0 when not limited). */
  retryAfterMs: number;
}

export interface HttpRequestRateLimiter {
  /** Check whether a request from this client key should be allowed and consume one slot. */
  consume(clientKey: string): HttpRateLimitCheckResult;
  /** Return the current number of tracked clients (for diagnostics). */
  size(): number;
  /** Remove expired entries and release memory. */
  prune(): void;
  /** Dispose the limiter and cancel periodic cleanup timers. */
  dispose(): void;
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_MAX_REQUESTS = 60;
const DEFAULT_WINDOW_MS = 60_000; // 1 minute
const PRUNE_INTERVAL_MS = 60_000;

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

/**
 * Resolve a stable client key from an HTTP request for rate-limiting purposes.
 * Uses the resolved client IP (accounting for trusted proxies and forwarded headers).
 */
export function resolveHttpRateLimitClientKey(
  req: IncomingMessage,
  trustedProxies?: string[],
  allowRealIpFallback?: boolean,
): string {
  const ip = resolveClientIp({
    remoteAddr: req.socket?.remoteAddress,
    forwardedFor: req.headers["x-forwarded-for"] as string | undefined,
    realIp: req.headers["x-real-ip"] as string | undefined,
    trustedProxies,
    allowRealIpFallback,
  });
  return ip ?? "unknown";
}

export function createHttpRequestRateLimiter(config?: HttpRateLimitConfig): HttpRequestRateLimiter {
  const maxRequests = config?.maxRequests ?? DEFAULT_MAX_REQUESTS;
  const windowMs = config?.windowMs ?? DEFAULT_WINDOW_MS;
  const exemptLoopback = config?.exemptLoopback ?? true;
  const pruneIntervalMs = config?.pruneIntervalMs ?? PRUNE_INTERVAL_MS;

  // Each entry stores an array of request timestamps within the current window.
  const entries = new Map<string, number[]>();

  const pruneTimer = pruneIntervalMs > 0 ? setInterval(() => prune(), pruneIntervalMs) : null;
  if (pruneTimer?.unref) {
    pruneTimer.unref();
  }

  function isExempt(clientKey: string): boolean {
    return exemptLoopback && isLoopbackAddress(clientKey);
  }

  function slideWindow(timestamps: number[], now: number): number[] {
    const cutoff = now - windowMs;
    return timestamps.filter((ts) => ts > cutoff);
  }

  function consume(clientKey: string): HttpRateLimitCheckResult {
    if (isExempt(clientKey)) {
      return { allowed: true, remaining: maxRequests, retryAfterMs: 0 };
    }

    const now = Date.now();
    let timestamps = entries.get(clientKey);

    if (!timestamps) {
      timestamps = [now];
      entries.set(clientKey, timestamps);
      return { allowed: true, remaining: maxRequests - 1, retryAfterMs: 0 };
    }

    // Slide window: drop expired entries.
    timestamps = slideWindow(timestamps, now);

    if (timestamps.length >= maxRequests) {
      // Denied: compute when the earliest request in the window will expire.
      const earliest = timestamps[0] ?? now;
      const retryAfterMs = Math.max(0, earliest + windowMs - now);
      entries.set(clientKey, timestamps);
      return { allowed: false, remaining: 0, retryAfterMs };
    }

    // Allowed: record this request.
    timestamps.push(now);
    entries.set(clientKey, timestamps);
    const remaining = Math.max(0, maxRequests - timestamps.length);
    return { allowed: true, remaining, retryAfterMs: 0 };
  }

  function prune(): void {
    const now = Date.now();
    for (const [key, timestamps] of entries) {
      const pruned = slideWindow(timestamps, now);
      if (pruned.length === 0) {
        entries.delete(key);
      } else {
        entries.set(key, pruned);
      }
    }
  }

  function size(): number {
    return entries.size;
  }

  function dispose(): void {
    if (pruneTimer) {
      clearInterval(pruneTimer);
    }
    entries.clear();
  }

  return { consume, size, prune, dispose };
}

// ---------------------------------------------------------------------------
// HTTP middleware helper
// ---------------------------------------------------------------------------

/**
 * Send a 429 response with standard rate-limit headers.
 */
export function sendHttpRateLimited(res: ServerResponse, result: HttpRateLimitCheckResult): void {
  const retryAfterSeconds = result.retryAfterMs > 0 ? Math.ceil(result.retryAfterMs / 1000) : 1;
  res.setHeader("Retry-After", String(retryAfterSeconds));
  res.setHeader("X-RateLimit-Remaining", "0");
  sendJson(res, 429, {
    error: {
      message: "Rate limit exceeded. Please retry after the indicated period.",
      type: "rate_limited",
    },
  });
}

/**
 * Convenience: check rate limit for an HTTP request and send 429 if exceeded.
 * Returns true if the request was rate-limited (caller should stop processing).
 * Returns false if the request is allowed to proceed.
 */
export function enforceHttpRateLimit(params: {
  req: IncomingMessage;
  res: ServerResponse;
  limiter: HttpRequestRateLimiter;
  trustedProxies?: string[];
  allowRealIpFallback?: boolean;
}): boolean {
  const clientKey = resolveHttpRateLimitClientKey(
    params.req,
    params.trustedProxies,
    params.allowRealIpFallback,
  );
  const result = params.limiter.consume(clientKey);
  if (!result.allowed) {
    sendHttpRateLimited(params.res, result);
    return true;
  }
  // Set informational headers on allowed responses.
  params.res.setHeader("X-RateLimit-Remaining", String(result.remaining));
  return false;
}
