/**
 * WebSocket connection count limiter (OC-SEC-025, OC-SEC-026).
 *
 * Prevents resource exhaustion by enforcing:
 * - A global cap on total active WebSocket connections (default: 200).
 * - A per-IP cap so a single client cannot monopolize all slots (default: 50).
 *
 * Loopback addresses (127.0.0.1 / ::1) are exempt from the per-IP limit
 * so that the local CLI is never rejected.
 *
 * Callers should invoke {@link WsConnectionLimiter.acquire} before completing
 * a WebSocket upgrade and {@link WsConnectionLimiter.release} when the
 * connection closes.
 */

import { isLoopbackAddress } from "./net.js";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export interface WsConnectionLimitConfig {
  /** Maximum total active WebSocket connections.  @default 200 */
  maxConnections?: number;
  /** Maximum active WebSocket connections from a single IP.  @default 50 */
  maxConnectionsPerIp?: number;
  /** Exempt loopback addresses from per-IP limits.  @default true */
  exemptLoopback?: boolean;
}

const DEFAULT_MAX_CONNECTIONS = 200;
const DEFAULT_MAX_CONNECTIONS_PER_IP = 50;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WsConnectionLimitResult =
  | { allowed: true }
  | { allowed: false; reason: "global-limit" | "per-ip-limit" };

export interface WsConnectionLimiter {
  /**
   * Try to acquire a connection slot. Returns `{ allowed: true }` if the
   * connection is permitted, otherwise a rejection reason. On success the
   * caller MUST eventually call {@link release} with the same `ip`.
   */
  acquire(ip: string | undefined): WsConnectionLimitResult;
  /** Release a connection slot previously acquired for `ip`. */
  release(ip: string | undefined): void;
  /** Current total active connection count. */
  totalConnections(): number;
  /** Current active connection count for a specific IP. */
  connectionsForIp(ip: string | undefined): number;
}

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

function normalizeIp(ip: string | undefined): string {
  if (!ip) {
    return "unknown";
  }
  // Normalize IPv4-mapped IPv6 (::ffff:127.0.0.1 -> 127.0.0.1).
  const mapped = ip.startsWith("::ffff:") ? ip.slice(7) : ip;
  return mapped.toLowerCase();
}

export function createWsConnectionLimiter(config?: WsConnectionLimitConfig): WsConnectionLimiter {
  const maxConnections = config?.maxConnections ?? DEFAULT_MAX_CONNECTIONS;
  const maxPerIp = config?.maxConnectionsPerIp ?? DEFAULT_MAX_CONNECTIONS_PER_IP;
  const exemptLoopback = config?.exemptLoopback ?? true;

  let total = 0;
  const perIp = new Map<string, number>();

  function acquire(rawIp: string | undefined): WsConnectionLimitResult {
    // Global limit always applies (including loopback).
    if (total >= maxConnections) {
      return { allowed: false, reason: "global-limit" };
    }

    const ip = normalizeIp(rawIp);
    const isExempt = exemptLoopback && isLoopbackAddress(ip);

    if (!isExempt) {
      const current = perIp.get(ip) ?? 0;
      if (current >= maxPerIp) {
        return { allowed: false, reason: "per-ip-limit" };
      }
    }

    // Commit the slot.
    total += 1;
    perIp.set(ip, (perIp.get(ip) ?? 0) + 1);

    return { allowed: true };
  }

  function release(rawIp: string | undefined): void {
    if (total > 0) {
      total -= 1;
    }

    const ip = normalizeIp(rawIp);
    const count = perIp.get(ip);
    if (count !== undefined) {
      if (count <= 1) {
        perIp.delete(ip);
      } else {
        perIp.set(ip, count - 1);
      }
    }
  }

  function totalConnections(): number {
    return total;
  }

  function connectionsForIp(rawIp: string | undefined): number {
    return perIp.get(normalizeIp(rawIp)) ?? 0;
  }

  return { acquire, release, totalConnections, connectionsForIp };
}
