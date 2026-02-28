import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  SETUP_CODE_MAX_AGE_MS,
  decodePairingSetupCode,
  encodePairingSetupCode,
  isSetupCodeExpired,
  resolvePairingSetupFromConfig,
} from "./setup-code.js";

describe("pairing setup code", () => {
  beforeEach(() => {
    vi.stubEnv("OPENCLAW_GATEWAY_TOKEN", "");
    vi.stubEnv("CLAWDBOT_GATEWAY_TOKEN", "");
    vi.stubEnv("OPENCLAW_GATEWAY_PASSWORD", "");
    vi.stubEnv("CLAWDBOT_GATEWAY_PASSWORD", "");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("encodes payload as base64url JSON with createdAt timestamp", () => {
    const now = 1700000000000;
    const code = encodePairingSetupCode({
      url: "wss://gateway.example.com:443",
      token: "abc",
      createdAt: now,
    });

    // Verify roundtrip: decode and check fields.
    const decoded = decodePairingSetupCode(code);
    expect(decoded).not.toBeNull();
    expect(decoded!.url).toBe("wss://gateway.example.com:443");
    expect(decoded!.token).toBe("abc");
    expect(decoded!.createdAt).toBe(now);

    // Verify base64url encoding (no +, /, or trailing =).
    expect(code).not.toMatch(/[+/=]/);
  });

  it("auto-stamps createdAt when not provided", () => {
    const before = Date.now();
    const code = encodePairingSetupCode({
      url: "wss://example.com",
      token: "tok",
    });
    const after = Date.now();

    const decoded = decodePairingSetupCode(code);
    expect(decoded).not.toBeNull();
    expect(decoded!.createdAt).toBeGreaterThanOrEqual(before);
    expect(decoded!.createdAt).toBeLessThanOrEqual(after);
  });

  it("decodePairingSetupCode returns null for invalid input", () => {
    expect(decodePairingSetupCode("")).toBeNull();
    expect(decodePairingSetupCode("not-base64!!!")).toBeNull();
    // Valid base64 but not a setup code JSON (missing url).
    const noUrl = Buffer.from(JSON.stringify({ token: "x" })).toString("base64");
    expect(decodePairingSetupCode(noUrl)).toBeNull();
  });

  it("isSetupCodeExpired returns false for fresh codes", () => {
    const now = Date.now();
    expect(isSetupCodeExpired({ url: "wss://x", createdAt: now }, SETUP_CODE_MAX_AGE_MS, now)).toBe(
      false,
    );
    // 59 minutes old: still valid.
    expect(
      isSetupCodeExpired(
        { url: "wss://x", createdAt: now - 59 * 60 * 1000 },
        SETUP_CODE_MAX_AGE_MS,
        now,
      ),
    ).toBe(false);
  });

  it("isSetupCodeExpired returns true for stale codes", () => {
    const now = Date.now();
    // 61 minutes old: expired.
    expect(
      isSetupCodeExpired(
        { url: "wss://x", createdAt: now - 61 * 60 * 1000 },
        SETUP_CODE_MAX_AGE_MS,
        now,
      ),
    ).toBe(true);
  });

  it("isSetupCodeExpired treats missing createdAt as expired", () => {
    expect(isSetupCodeExpired({ url: "wss://x" })).toBe(true);
  });

  it("resolves custom bind + token auth", async () => {
    const resolved = await resolvePairingSetupFromConfig({
      gateway: {
        bind: "custom",
        customBindHost: "gateway.local",
        port: 19001,
        auth: { mode: "token", token: "tok_123" },
      },
    });

    expect(resolved).toEqual({
      ok: true,
      payload: {
        url: "ws://gateway.local:19001",
        token: "tok_123",
        password: undefined,
      },
      authLabel: "token",
      urlSource: "gateway.bind=custom",
    });
  });

  it("honors env token override", async () => {
    const resolved = await resolvePairingSetupFromConfig(
      {
        gateway: {
          bind: "custom",
          customBindHost: "gateway.local",
          auth: { mode: "token", token: "old" },
        },
      },
      {
        env: {
          OPENCLAW_GATEWAY_TOKEN: "new-token",
        },
      },
    );

    expect(resolved.ok).toBe(true);
    if (!resolved.ok) {
      throw new Error("expected setup resolution to succeed");
    }
    expect(resolved.payload.token).toBe("new-token");
  });

  it("errors when gateway is loopback only", async () => {
    const resolved = await resolvePairingSetupFromConfig({
      gateway: {
        bind: "loopback",
        auth: { mode: "token", token: "tok" },
      },
    });

    expect(resolved.ok).toBe(false);
    if (resolved.ok) {
      throw new Error("expected setup resolution to fail");
    }
    expect(resolved.error).toContain("only bound to loopback");
  });

  it("uses tailscale serve DNS when available", async () => {
    const runCommandWithTimeout = vi.fn(async () => ({
      code: 0,
      stdout: '{"Self":{"DNSName":"mb-server.tailnet.ts.net."}}',
      stderr: "",
    }));

    const resolved = await resolvePairingSetupFromConfig(
      {
        gateway: {
          tailscale: { mode: "serve" },
          auth: { mode: "password", password: "secret" },
        },
      },
      {
        runCommandWithTimeout,
      },
    );

    expect(resolved).toEqual({
      ok: true,
      payload: {
        url: "wss://mb-server.tailnet.ts.net",
        token: undefined,
        password: "secret",
      },
      authLabel: "password",
      urlSource: "gateway.tailscale.mode=serve",
    });
  });

  it("prefers gateway.remote.url over tailscale when requested", async () => {
    const runCommandWithTimeout = vi.fn(async () => ({
      code: 0,
      stdout: '{"Self":{"DNSName":"mb-server.tailnet.ts.net."}}',
      stderr: "",
    }));

    const resolved = await resolvePairingSetupFromConfig(
      {
        gateway: {
          tailscale: { mode: "serve" },
          remote: { url: "wss://remote.example.com:444" },
          auth: { mode: "token", token: "tok_123" },
        },
      },
      {
        preferRemoteUrl: true,
        runCommandWithTimeout,
      },
    );

    expect(resolved).toEqual({
      ok: true,
      payload: {
        url: "wss://remote.example.com:444",
        token: "tok_123",
        password: undefined,
      },
      authLabel: "token",
      urlSource: "gateway.remote.url",
    });
    expect(runCommandWithTimeout).not.toHaveBeenCalled();
  });
});
