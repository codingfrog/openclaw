import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createHttpRequestRateLimiter,
  type HttpRequestRateLimiter,
} from "./http-request-rate-limit.js";

describe("http request rate limiter", () => {
  let limiter: HttpRequestRateLimiter;

  afterEach(() => {
    limiter?.dispose();
  });

  // ---------- basic sliding window ----------

  it("allows requests when under the limit", () => {
    limiter = createHttpRequestRateLimiter({ maxRequests: 5, windowMs: 60_000 });
    const result = limiter.consume("10.0.0.1");
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(4);
    expect(result.retryAfterMs).toBe(0);
  });

  it("decrements remaining count after each request", () => {
    limiter = createHttpRequestRateLimiter({ maxRequests: 3, windowMs: 60_000 });
    expect(limiter.consume("10.0.0.1").remaining).toBe(2);
    expect(limiter.consume("10.0.0.1").remaining).toBe(1);
    expect(limiter.consume("10.0.0.1").remaining).toBe(0);
  });

  it("blocks when maxRequests is reached", () => {
    limiter = createHttpRequestRateLimiter({ maxRequests: 2, windowMs: 60_000 });
    expect(limiter.consume("10.0.0.2").allowed).toBe(true);
    expect(limiter.consume("10.0.0.2").allowed).toBe(true);

    const result = limiter.consume("10.0.0.2");
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
    expect(result.retryAfterMs).toBeGreaterThan(0);
    expect(result.retryAfterMs).toBeLessThanOrEqual(60_000);
  });

  // ---------- sliding window expiry ----------

  it("allows requests again after window expires", () => {
    vi.useFakeTimers();
    try {
      limiter = createHttpRequestRateLimiter({ maxRequests: 2, windowMs: 10_000 });
      expect(limiter.consume("10.0.0.3").allowed).toBe(true);
      expect(limiter.consume("10.0.0.3").allowed).toBe(true);
      expect(limiter.consume("10.0.0.3").allowed).toBe(false);

      // Advance past the window so old requests expire.
      vi.advanceTimersByTime(11_000);
      const result = limiter.consume("10.0.0.3");
      expect(result.allowed).toBe(true);
      expect(result.remaining).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("slides the window correctly for partial expiry", () => {
    vi.useFakeTimers();
    try {
      limiter = createHttpRequestRateLimiter({ maxRequests: 3, windowMs: 10_000 });

      // T=0: first request
      limiter.consume("10.0.0.4");
      // T=5s: second request
      vi.advanceTimersByTime(5_000);
      limiter.consume("10.0.0.4");
      // T=8s: third request (at capacity)
      vi.advanceTimersByTime(3_000);
      limiter.consume("10.0.0.4");
      expect(limiter.consume("10.0.0.4").allowed).toBe(false);

      // T=11s: first request expires (T=0 + 10s window), one slot opens
      vi.advanceTimersByTime(3_000);
      const result = limiter.consume("10.0.0.4");
      expect(result.allowed).toBe(true);
      expect(result.remaining).toBe(0); // 2 remaining + this new one = at capacity again
    } finally {
      vi.useRealTimers();
    }
  });

  // ---------- per-client isolation ----------

  it("tracks clients independently", () => {
    limiter = createHttpRequestRateLimiter({ maxRequests: 1, windowMs: 60_000 });
    expect(limiter.consume("10.0.0.10").allowed).toBe(true);
    expect(limiter.consume("10.0.0.10").allowed).toBe(false);

    // A different client should be unaffected.
    expect(limiter.consume("10.0.0.11").allowed).toBe(true);
  });

  // ---------- loopback exemption ----------

  it("exempts loopback addresses by default", () => {
    limiter = createHttpRequestRateLimiter({ maxRequests: 1, windowMs: 60_000 });
    expect(limiter.consume("127.0.0.1").allowed).toBe(true);
    expect(limiter.consume("127.0.0.1").allowed).toBe(true);
    expect(limiter.consume("127.0.0.1").remaining).toBe(1);
  });

  it("exempts IPv6 loopback by default", () => {
    limiter = createHttpRequestRateLimiter({ maxRequests: 1, windowMs: 60_000 });
    expect(limiter.consume("::1").allowed).toBe(true);
    expect(limiter.consume("::1").allowed).toBe(true);
  });

  it("rate-limits loopback when exemptLoopback is false", () => {
    limiter = createHttpRequestRateLimiter({
      maxRequests: 1,
      windowMs: 60_000,
      exemptLoopback: false,
    });
    expect(limiter.consume("127.0.0.1").allowed).toBe(true);
    expect(limiter.consume("127.0.0.1").allowed).toBe(false);
  });

  // ---------- prune ----------

  it("prune removes stale entries", () => {
    vi.useFakeTimers();
    try {
      limiter = createHttpRequestRateLimiter({ maxRequests: 10, windowMs: 5_000 });
      limiter.consume("10.0.0.30");
      expect(limiter.size()).toBe(1);

      vi.advanceTimersByTime(6_000);
      limiter.prune();
      expect(limiter.size()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("prune keeps entries with active requests in window", () => {
    vi.useFakeTimers();
    try {
      limiter = createHttpRequestRateLimiter({ maxRequests: 10, windowMs: 10_000 });
      limiter.consume("10.0.0.31");

      // Only 5s have passed; request is still in window.
      vi.advanceTimersByTime(5_000);
      limiter.prune();
      expect(limiter.size()).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  // ---------- dispose ----------

  it("dispose clears all entries", () => {
    limiter = createHttpRequestRateLimiter();
    limiter.consume("10.0.0.40");
    expect(limiter.size()).toBe(1);
    limiter.dispose();
    expect(limiter.size()).toBe(0);
  });

  // ---------- retry-after accuracy ----------

  it("returns meaningful retryAfterMs when blocked", () => {
    vi.useFakeTimers();
    try {
      limiter = createHttpRequestRateLimiter({ maxRequests: 1, windowMs: 30_000 });
      limiter.consume("10.0.0.50");

      // 10s later, try again.
      vi.advanceTimersByTime(10_000);
      const result = limiter.consume("10.0.0.50");
      expect(result.allowed).toBe(false);
      // The earliest request was at T=0, window is 30s, so retry after ~20s.
      expect(result.retryAfterMs).toBeGreaterThanOrEqual(19_000);
      expect(result.retryAfterMs).toBeLessThanOrEqual(20_001);
    } finally {
      vi.useRealTimers();
    }
  });

  // ---------- defaults ----------

  it("uses default maxRequests of 60", () => {
    limiter = createHttpRequestRateLimiter();
    const result = limiter.consume("10.0.0.60");
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(59);
  });
});
