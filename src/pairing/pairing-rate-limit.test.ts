import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPairingRateLimiter, type PairingRateLimiter } from "./pairing-rate-limit.js";

describe("pairing rate limiter", () => {
  let limiter: PairingRateLimiter;

  beforeEach(() => {
    vi.useFakeTimers();
    limiter = createPairingRateLimiter({
      maxAttempts: 5,
      windowMs: 600_000,
      lockoutMs: 600_000,
      pruneIntervalMs: 0, // disable auto-prune in tests
    });
  });

  afterEach(() => {
    limiter.dispose();
    vi.useRealTimers();
  });

  it("allows attempts up to the max threshold", () => {
    for (let i = 0; i < 4; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    const result = limiter.check("telegram", "sender1");
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(1);
  });

  it("blocks after exceeding the max attempts threshold", () => {
    for (let i = 0; i < 5; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    const result = limiter.check("telegram", "sender1");
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
    expect(result.retryAfterMs).toBeGreaterThan(0);
  });

  it("isolates rate limits by channel", () => {
    for (let i = 0; i < 5; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    const blocked = limiter.check("telegram", "sender1");
    expect(blocked.allowed).toBe(false);

    // Same sender on a different channel should still be allowed.
    const allowed = limiter.check("discord", "sender1");
    expect(allowed.allowed).toBe(true);
    expect(allowed.remaining).toBe(5);
  });

  it("isolates rate limits by sender", () => {
    for (let i = 0; i < 5; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    const blocked = limiter.check("telegram", "sender1");
    expect(blocked.allowed).toBe(false);

    // Different sender on the same channel should still be allowed.
    const allowed = limiter.check("telegram", "sender2");
    expect(allowed.allowed).toBe(true);
    expect(allowed.remaining).toBe(5);
  });

  it("unblocks after the lockout window expires", () => {
    for (let i = 0; i < 5; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    expect(limiter.check("telegram", "sender1").allowed).toBe(false);

    // Advance past the lockout window.
    vi.advanceTimersByTime(600_001);
    const result = limiter.check("telegram", "sender1");
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(5);
  });

  it("resets rate limit state on successful approval", () => {
    for (let i = 0; i < 4; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    expect(limiter.check("telegram", "sender1").remaining).toBe(1);

    limiter.reset("telegram", "sender1");
    const result = limiter.check("telegram", "sender1");
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(5);
  });

  it("slides the window and drops old attempts", () => {
    // Record 3 failures now.
    for (let i = 0; i < 3; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    // Advance time past the window.
    vi.advanceTimersByTime(600_001);
    // Old attempts should have fallen off; full budget restored.
    const result = limiter.check("telegram", "sender1");
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(5);
  });

  it("prunes stale entries", () => {
    limiter.recordFailure("telegram", "sender1");
    expect(limiter.size()).toBe(1);

    // Advance past window so the entry is stale.
    vi.advanceTimersByTime(600_001);
    limiter.prune();
    expect(limiter.size()).toBe(0);
  });

  it("does not prune locked entries before lockout expires", () => {
    for (let i = 0; i < 5; i++) {
      limiter.recordFailure("telegram", "sender1");
    }
    expect(limiter.size()).toBe(1);

    // Advance partway through lockout.
    vi.advanceTimersByTime(300_000);
    limiter.prune();
    expect(limiter.size()).toBe(1);
  });

  it("normalizes channel and sender keys to be case-insensitive", () => {
    limiter.recordFailure("Telegram", "Sender1");
    limiter.recordFailure("TELEGRAM", "SENDER1");
    limiter.recordFailure("telegram", "sender1");

    const result = limiter.check("telegram", "sender1");
    // All 3 failures should count toward the same bucket.
    expect(result.remaining).toBe(2);
  });

  it("returns full budget for unknown channel+sender", () => {
    const result = limiter.check("unknown", "nobody");
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(5);
    expect(result.retryAfterMs).toBe(0);
  });
});
