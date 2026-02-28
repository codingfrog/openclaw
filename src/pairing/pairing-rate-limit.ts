/**
 * In-memory sliding-window rate limiter for pairing code verification attempts.
 *
 * Prevents brute-force guessing of pairing codes by tracking failed approval
 * attempts per {channel, sender} key. After exceeding the threshold, further
 * attempts are rejected until the lockout expires.
 *
 * Design mirrors `src/gateway/auth-rate-limit.ts` but is keyed on
 * channel+sender rather than scope+IP, and uses tighter defaults suited to
 * the small pairing-code keyspace.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PairingRateLimitConfig {
  /** Maximum failed attempts before blocking.  @default 5 */
  maxAttempts?: number;
  /** Sliding window duration in milliseconds.  @default 600_000 (10 min) */
  windowMs?: number;
  /** Lockout duration in milliseconds after the limit is exceeded.  @default 600_000 (10 min) */
  lockoutMs?: number;
  /** Background prune interval in milliseconds; set <= 0 to disable auto-prune.  @default 60_000 */
  pruneIntervalMs?: number;
}

export interface PairingRateLimitEntry {
  /** Timestamps (epoch ms) of recent failed attempts inside the window. */
  attempts: number[];
  /** If set, requests are blocked until this epoch-ms instant. */
  lockedUntil?: number;
}

export interface PairingRateLimitCheckResult {
  /** Whether the request is allowed to proceed. */
  allowed: boolean;
  /** Number of remaining attempts before the limit is reached. */
  remaining: number;
  /** Milliseconds until the lockout expires (0 when not locked). */
  retryAfterMs: number;
}

export interface PairingRateLimiter {
  /** Check whether a channel+sender combination is allowed to attempt approval. */
  check(channel: string, senderId: string): PairingRateLimitCheckResult;
  /** Record a failed pairing code attempt. */
  recordFailure(channel: string, senderId: string): void;
  /** Reset the rate-limit state (e.g. after a successful approval). */
  reset(channel: string, senderId: string): void;
  /** Return the current number of tracked keys (diagnostics). */
  size(): number;
  /** Remove expired entries and release memory. */
  prune(): void;
  /** Dispose the limiter and cancel periodic cleanup timers. */
  dispose(): void;
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_MAX_ATTEMPTS = 5;
const DEFAULT_WINDOW_MS = 600_000; // 10 minutes
const DEFAULT_LOCKOUT_MS = 600_000; // 10 minutes
const PRUNE_INTERVAL_MS = 60_000;

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

function resolveKey(channel: string, senderId: string): string {
  return `${channel.trim().toLowerCase()}:${senderId.trim().toLowerCase()}`;
}

export function createPairingRateLimiter(config?: PairingRateLimitConfig): PairingRateLimiter {
  const maxAttempts = config?.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const windowMs = config?.windowMs ?? DEFAULT_WINDOW_MS;
  const lockoutMs = config?.lockoutMs ?? DEFAULT_LOCKOUT_MS;
  const pruneIntervalMs = config?.pruneIntervalMs ?? PRUNE_INTERVAL_MS;

  const entries = new Map<string, PairingRateLimitEntry>();

  // Periodic cleanup to avoid unbounded map growth.
  const pruneTimer = pruneIntervalMs > 0 ? setInterval(() => prune(), pruneIntervalMs) : null;
  if (pruneTimer?.unref) {
    pruneTimer.unref();
  }

  function slideWindow(entry: PairingRateLimitEntry, now: number): void {
    const cutoff = now - windowMs;
    entry.attempts = entry.attempts.filter((ts) => ts > cutoff);
  }

  function check(channel: string, senderId: string): PairingRateLimitCheckResult {
    const key = resolveKey(channel, senderId);
    const now = Date.now();
    const entry = entries.get(key);

    if (!entry) {
      return { allowed: true, remaining: maxAttempts, retryAfterMs: 0 };
    }

    // Still locked out?
    if (entry.lockedUntil && now < entry.lockedUntil) {
      return {
        allowed: false,
        remaining: 0,
        retryAfterMs: entry.lockedUntil - now,
      };
    }

    // Lockout expired -- clear it.
    if (entry.lockedUntil && now >= entry.lockedUntil) {
      entry.lockedUntil = undefined;
      entry.attempts = [];
    }

    slideWindow(entry, now);
    const remaining = Math.max(0, maxAttempts - entry.attempts.length);
    return { allowed: remaining > 0, remaining, retryAfterMs: 0 };
  }

  function recordFailure(channel: string, senderId: string): void {
    const key = resolveKey(channel, senderId);
    const now = Date.now();
    let entry = entries.get(key);

    if (!entry) {
      entry = { attempts: [] };
      entries.set(key, entry);
    }

    // If currently locked, do nothing (already blocked).
    if (entry.lockedUntil && now < entry.lockedUntil) {
      return;
    }

    slideWindow(entry, now);
    entry.attempts.push(now);

    if (entry.attempts.length >= maxAttempts) {
      entry.lockedUntil = now + lockoutMs;
    }
  }

  function reset(channel: string, senderId: string): void {
    const key = resolveKey(channel, senderId);
    entries.delete(key);
  }

  function prune(): void {
    const now = Date.now();
    for (const [key, entry] of entries) {
      if (entry.lockedUntil && now < entry.lockedUntil) {
        continue;
      }
      slideWindow(entry, now);
      if (entry.attempts.length === 0) {
        entries.delete(key);
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

  return { check, recordFailure, reset, size, prune, dispose };
}

// ---------------------------------------------------------------------------
// Singleton for the pairing-store module
// ---------------------------------------------------------------------------

let defaultInstance: PairingRateLimiter | null = null;

/**
 * Returns the process-wide pairing rate limiter singleton.
 * Lazy-initialized on first call so tests can replace it via
 * {@link setPairingRateLimiter}.
 */
export function getPairingRateLimiter(): PairingRateLimiter {
  if (!defaultInstance) {
    defaultInstance = createPairingRateLimiter();
  }
  return defaultInstance;
}

/**
 * Replace the process-wide pairing rate limiter (primarily for tests).
 * Disposes the previous instance if one existed.
 */
export function setPairingRateLimiter(limiter: PairingRateLimiter | null): void {
  if (defaultInstance && defaultInstance !== limiter) {
    defaultInstance.dispose();
  }
  defaultInstance = limiter;
}
