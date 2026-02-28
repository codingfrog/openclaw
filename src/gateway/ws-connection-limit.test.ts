import { describe, expect, it } from "vitest";
import { createWsConnectionLimiter } from "./ws-connection-limit.js";

describe("ws connection limiter", () => {
  // ---------- global limit ----------

  it("allows connections under the global limit", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 3, maxConnectionsPerIp: 10 });
    expect(limiter.acquire("10.0.0.1")).toEqual({ allowed: true });
    expect(limiter.acquire("10.0.0.2")).toEqual({ allowed: true });
    expect(limiter.acquire("10.0.0.3")).toEqual({ allowed: true });
    expect(limiter.totalConnections()).toBe(3);
  });

  it("rejects when global limit is reached", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 2, maxConnectionsPerIp: 10 });
    limiter.acquire("10.0.0.1");
    limiter.acquire("10.0.0.2");
    const result = limiter.acquire("10.0.0.3");
    expect(result).toEqual({ allowed: false, reason: "global-limit" });
    expect(limiter.totalConnections()).toBe(2);
  });

  it("allows new connections after releasing slots", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 2, maxConnectionsPerIp: 10 });
    limiter.acquire("10.0.0.1");
    limiter.acquire("10.0.0.2");
    limiter.release("10.0.0.1");
    expect(limiter.totalConnections()).toBe(1);
    const result = limiter.acquire("10.0.0.3");
    expect(result).toEqual({ allowed: true });
    expect(limiter.totalConnections()).toBe(2);
  });

  // ---------- per-IP limit ----------

  it("rejects when per-IP limit is reached", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 100, maxConnectionsPerIp: 2 });
    limiter.acquire("10.0.0.1");
    limiter.acquire("10.0.0.1");
    const result = limiter.acquire("10.0.0.1");
    expect(result).toEqual({ allowed: false, reason: "per-ip-limit" });
    expect(limiter.totalConnections()).toBe(2);
  });

  it("per-IP limit does not affect other IPs", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 100, maxConnectionsPerIp: 1 });
    limiter.acquire("10.0.0.1");
    expect(limiter.acquire("10.0.0.1")).toEqual({ allowed: false, reason: "per-ip-limit" });
    expect(limiter.acquire("10.0.0.2")).toEqual({ allowed: true });
  });

  it("per-IP counter decrements on release", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 100, maxConnectionsPerIp: 1 });
    limiter.acquire("10.0.0.1");
    limiter.release("10.0.0.1");
    expect(limiter.connectionsForIp("10.0.0.1")).toBe(0);
    expect(limiter.acquire("10.0.0.1")).toEqual({ allowed: true });
  });

  // ---------- loopback exemption ----------

  it("exempts loopback from per-IP limit by default", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 100, maxConnectionsPerIp: 1 });
    limiter.acquire("127.0.0.1");
    // Second connection from loopback should still be allowed.
    expect(limiter.acquire("127.0.0.1")).toEqual({ allowed: true });
    expect(limiter.totalConnections()).toBe(2);
  });

  it("exempts ::1 from per-IP limit by default", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 100, maxConnectionsPerIp: 1 });
    limiter.acquire("::1");
    expect(limiter.acquire("::1")).toEqual({ allowed: true });
  });

  it("exempts IPv4-mapped loopback from per-IP limit", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 100, maxConnectionsPerIp: 1 });
    limiter.acquire("::ffff:127.0.0.1");
    expect(limiter.acquire("::ffff:127.0.0.1")).toEqual({ allowed: true });
  });

  it("loopback is still subject to global limit", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 2, maxConnectionsPerIp: 1 });
    limiter.acquire("127.0.0.1");
    limiter.acquire("127.0.0.1");
    expect(limiter.acquire("127.0.0.1")).toEqual({ allowed: false, reason: "global-limit" });
  });

  it("enforces per-IP on loopback when exemptLoopback is false", () => {
    const limiter = createWsConnectionLimiter({
      maxConnections: 100,
      maxConnectionsPerIp: 1,
      exemptLoopback: false,
    });
    limiter.acquire("127.0.0.1");
    expect(limiter.acquire("127.0.0.1")).toEqual({ allowed: false, reason: "per-ip-limit" });
  });

  // ---------- defaults ----------

  it("uses sensible defaults when no config is provided", () => {
    const limiter = createWsConnectionLimiter();
    // Should allow at least a few connections without issues.
    for (let i = 0; i < 50; i++) {
      expect(limiter.acquire("10.0.0.1")).toEqual({ allowed: true });
    }
    // 50 per-IP should be the default limit.
    expect(limiter.acquire("10.0.0.1")).toEqual({ allowed: false, reason: "per-ip-limit" });
  });

  // ---------- release underflow safety ----------

  it("release does not go below zero", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 10, maxConnectionsPerIp: 10 });
    limiter.release("10.0.0.1");
    limiter.release("10.0.0.1");
    expect(limiter.totalConnections()).toBe(0);
    expect(limiter.connectionsForIp("10.0.0.1")).toBe(0);
  });

  // ---------- undefined IP ----------

  it("handles undefined IP gracefully", () => {
    const limiter = createWsConnectionLimiter({ maxConnections: 100, maxConnectionsPerIp: 2 });
    expect(limiter.acquire(undefined)).toEqual({ allowed: true });
    expect(limiter.acquire(undefined)).toEqual({ allowed: true });
    expect(limiter.acquire(undefined)).toEqual({ allowed: false, reason: "per-ip-limit" });
    limiter.release(undefined);
    expect(limiter.acquire(undefined)).toEqual({ allowed: true });
  });
});
