import os from "node:os";
import type { OpenClawConfig } from "../config/types.js";
import { resolveGatewayBindUrl } from "../shared/gateway-bind-url.js";
import { isCarrierGradeNatIpv4Address, isRfc1918Ipv4Address } from "../shared/net/ip.js";
import { resolveTailnetHostWithRunner } from "../shared/tailscale-status.js";

const DEFAULT_GATEWAY_PORT = 18789;

/**
 * Default maximum age for pairing setup codes: 1 hour in milliseconds.
 * Consuming clients should reject setup codes older than this to limit the
 * window during which a leaked code can be used.
 */
export const SETUP_CODE_MAX_AGE_MS = 60 * 60 * 1000;

export type PairingSetupPayload = {
  url: string;
  token?: string;
  password?: string;
  /**
   * Unix epoch timestamp (ms) when this setup code was generated.
   * Consumers should reject codes where `Date.now() - createdAt > SETUP_CODE_MAX_AGE_MS`.
   */
  createdAt?: number;
};

export type PairingSetupCommandResult = {
  code: number | null;
  stdout: string;
  stderr?: string;
};

export type PairingSetupCommandRunner = (
  argv: string[],
  opts: { timeoutMs: number },
) => Promise<PairingSetupCommandResult>;

export type ResolvePairingSetupOptions = {
  env?: NodeJS.ProcessEnv;
  publicUrl?: string;
  preferRemoteUrl?: boolean;
  forceSecure?: boolean;
  runCommandWithTimeout?: PairingSetupCommandRunner;
  networkInterfaces?: () => ReturnType<typeof os.networkInterfaces>;
};

export type PairingSetupResolution =
  | {
      ok: true;
      payload: PairingSetupPayload;
      authLabel: "token" | "password";
      urlSource: string;
    }
  | {
      ok: false;
      error: string;
    };

type ResolveUrlResult = {
  url?: string;
  source?: string;
  error?: string;
};

type ResolveAuthResult = {
  token?: string;
  password?: string;
  label?: "token" | "password";
  error?: string;
};

function normalizeUrl(raw: string, schemeFallback: "ws" | "wss"): string | null {
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  try {
    const parsed = new URL(trimmed);
    const scheme = parsed.protocol.replace(":", "");
    if (!scheme) {
      return null;
    }
    const resolvedScheme = scheme === "http" ? "ws" : scheme === "https" ? "wss" : scheme;
    if (resolvedScheme !== "ws" && resolvedScheme !== "wss") {
      return null;
    }
    const host = parsed.hostname;
    if (!host) {
      return null;
    }
    const port = parsed.port ? `:${parsed.port}` : "";
    return `${resolvedScheme}://${host}${port}`;
  } catch {
    // Fall through to host:port parsing.
  }

  const withoutPath = trimmed.split("/")[0] ?? "";
  if (!withoutPath) {
    return null;
  }
  return `${schemeFallback}://${withoutPath}`;
}

function resolveGatewayPort(cfg: OpenClawConfig, env: NodeJS.ProcessEnv): number {
  const envRaw = env.OPENCLAW_GATEWAY_PORT?.trim() || env.CLAWDBOT_GATEWAY_PORT?.trim();
  if (envRaw) {
    const parsed = Number.parseInt(envRaw, 10);
    if (Number.isFinite(parsed) && parsed > 0) {
      return parsed;
    }
  }
  const configPort = cfg.gateway?.port;
  if (typeof configPort === "number" && Number.isFinite(configPort) && configPort > 0) {
    return configPort;
  }
  return DEFAULT_GATEWAY_PORT;
}

function resolveScheme(
  cfg: OpenClawConfig,
  opts?: {
    forceSecure?: boolean;
  },
): "ws" | "wss" {
  if (opts?.forceSecure) {
    return "wss";
  }
  return cfg.gateway?.tls?.enabled === true ? "wss" : "ws";
}

function isPrivateIPv4(address: string): boolean {
  return isRfc1918Ipv4Address(address);
}

function isTailnetIPv4(address: string): boolean {
  return isCarrierGradeNatIpv4Address(address);
}

function pickIPv4Matching(
  networkInterfaces: () => ReturnType<typeof os.networkInterfaces>,
  matches: (address: string) => boolean,
): string | null {
  const nets = networkInterfaces();
  for (const entries of Object.values(nets)) {
    if (!entries) {
      continue;
    }
    for (const entry of entries) {
      const family = entry?.family;
      const isIpv4 = family === "IPv4";
      if (!entry || entry.internal || !isIpv4) {
        continue;
      }
      const address = entry.address?.trim() ?? "";
      if (!address) {
        continue;
      }
      if (matches(address)) {
        return address;
      }
    }
  }
  return null;
}

function pickLanIPv4(
  networkInterfaces: () => ReturnType<typeof os.networkInterfaces>,
): string | null {
  return pickIPv4Matching(networkInterfaces, isPrivateIPv4);
}

function pickTailnetIPv4(
  networkInterfaces: () => ReturnType<typeof os.networkInterfaces>,
): string | null {
  return pickIPv4Matching(networkInterfaces, isTailnetIPv4);
}

function resolveAuth(cfg: OpenClawConfig, env: NodeJS.ProcessEnv): ResolveAuthResult {
  const mode = cfg.gateway?.auth?.mode;
  const token =
    env.OPENCLAW_GATEWAY_TOKEN?.trim() ||
    env.CLAWDBOT_GATEWAY_TOKEN?.trim() ||
    cfg.gateway?.auth?.token?.trim();
  const password =
    env.OPENCLAW_GATEWAY_PASSWORD?.trim() ||
    env.CLAWDBOT_GATEWAY_PASSWORD?.trim() ||
    cfg.gateway?.auth?.password?.trim();

  if (mode === "password") {
    if (!password) {
      return { error: "Gateway auth is set to password, but no password is configured." };
    }
    return { password, label: "password" };
  }
  if (mode === "token") {
    if (!token) {
      return { error: "Gateway auth is set to token, but no token is configured." };
    }
    return { token, label: "token" };
  }
  if (token) {
    return { token, label: "token" };
  }
  if (password) {
    return { password, label: "password" };
  }
  return { error: "Gateway auth is not configured (no token or password)." };
}

async function resolveGatewayUrl(
  cfg: OpenClawConfig,
  opts: {
    env: NodeJS.ProcessEnv;
    publicUrl?: string;
    preferRemoteUrl?: boolean;
    forceSecure?: boolean;
    runCommandWithTimeout?: PairingSetupCommandRunner;
    networkInterfaces: () => ReturnType<typeof os.networkInterfaces>;
  },
): Promise<ResolveUrlResult> {
  const scheme = resolveScheme(cfg, { forceSecure: opts.forceSecure });
  const port = resolveGatewayPort(cfg, opts.env);

  if (typeof opts.publicUrl === "string" && opts.publicUrl.trim()) {
    const url = normalizeUrl(opts.publicUrl, scheme);
    if (url) {
      return { url, source: "plugins.entries.device-pair.config.publicUrl" };
    }
    return { error: "Configured publicUrl is invalid." };
  }

  const remoteUrlRaw = cfg.gateway?.remote?.url;
  const remoteUrl =
    typeof remoteUrlRaw === "string" && remoteUrlRaw.trim()
      ? normalizeUrl(remoteUrlRaw, scheme)
      : null;
  if (opts.preferRemoteUrl && remoteUrl) {
    return { url: remoteUrl, source: "gateway.remote.url" };
  }

  const tailscaleMode = cfg.gateway?.tailscale?.mode ?? "off";
  if (tailscaleMode === "serve" || tailscaleMode === "funnel") {
    const host = await resolveTailnetHostWithRunner(opts.runCommandWithTimeout);
    if (!host) {
      return { error: "Tailscale Serve is enabled, but MagicDNS could not be resolved." };
    }
    return { url: `wss://${host}`, source: `gateway.tailscale.mode=${tailscaleMode}` };
  }

  if (remoteUrl) {
    return { url: remoteUrl, source: "gateway.remote.url" };
  }

  const bindResult = resolveGatewayBindUrl({
    bind: cfg.gateway?.bind,
    customBindHost: cfg.gateway?.customBindHost,
    scheme,
    port,
    pickTailnetHost: () => pickTailnetIPv4(opts.networkInterfaces),
    pickLanHost: () => pickLanIPv4(opts.networkInterfaces),
  });
  if (bindResult) {
    return bindResult;
  }

  return {
    error:
      "Gateway is only bound to loopback. Set gateway.bind=lan, enable tailscale serve, or configure plugins.entries.device-pair.config.publicUrl.",
  };
}

/**
 * Encode a pairing payload as a base64url string suitable for QR codes.
 *
 * **OC-SEC-009 – Security notice:**
 * The resulting setup code contains the long-lived gateway credential (token
 * or password) in cleartext -- it is only base64-encoded, NOT encrypted.
 * Anyone who obtains this string can decode it trivially and extract the
 * credential. Treat setup codes with the same sensitivity as raw passwords:
 *   - Do not transmit over insecure channels (plain HTTP, unencrypted email).
 *   - Do not persist to logs or analytics.
 *   - Display only when the user explicitly requests pairing.
 *
 * A `createdAt` timestamp is embedded so consumers can reject stale codes.
 */
export function encodePairingSetupCode(payload: PairingSetupPayload): string {
  const stamped: PairingSetupPayload = {
    ...payload,
    createdAt: payload.createdAt ?? Date.now(),
  };
  const json = JSON.stringify(stamped);
  const base64 = Buffer.from(json, "utf8").toString("base64");
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

/**
 * Decode a base64url-encoded setup code back into a {@link PairingSetupPayload}.
 * Returns `null` when the input is malformed or not valid JSON.
 */
export function decodePairingSetupCode(raw: string): PairingSetupPayload | null {
  try {
    const trimmed = raw.trim();
    if (!trimmed) {
      return null;
    }
    const normalized = trimmed.replace(/-/g, "+").replace(/_/g, "/");
    const pad = normalized.length % 4;
    const padded = pad === 0 ? normalized : normalized + "=".repeat(4 - pad);
    const json = Buffer.from(padded, "base64").toString("utf8");
    const parsed = JSON.parse(json) as Record<string, unknown>;
    if (typeof parsed !== "object" || parsed === null || typeof parsed.url !== "string") {
      return null;
    }
    return parsed as unknown as PairingSetupPayload;
  } catch {
    return null;
  }
}

/**
 * Returns `true` when a setup code's `createdAt` timestamp indicates the code
 * is older than {@link SETUP_CODE_MAX_AGE_MS} (default: 1 hour).
 * Codes without a `createdAt` field are treated as expired (conservative).
 */
export function isSetupCodeExpired(
  payload: PairingSetupPayload,
  maxAgeMs: number = SETUP_CODE_MAX_AGE_MS,
  now: number = Date.now(),
): boolean {
  if (typeof payload.createdAt !== "number") {
    return true;
  }
  return now - payload.createdAt > maxAgeMs;
}

export async function resolvePairingSetupFromConfig(
  cfg: OpenClawConfig,
  options: ResolvePairingSetupOptions = {},
): Promise<PairingSetupResolution> {
  const env = options.env ?? process.env;
  const auth = resolveAuth(cfg, env);
  if (auth.error) {
    return { ok: false, error: auth.error };
  }

  const urlResult = await resolveGatewayUrl(cfg, {
    env,
    publicUrl: options.publicUrl,
    preferRemoteUrl: options.preferRemoteUrl,
    forceSecure: options.forceSecure,
    runCommandWithTimeout: options.runCommandWithTimeout,
    networkInterfaces: options.networkInterfaces ?? os.networkInterfaces,
  });

  if (!urlResult.url) {
    return { ok: false, error: urlResult.error ?? "Gateway URL unavailable." };
  }

  if (!auth.label) {
    return { ok: false, error: "Gateway auth is not configured (no token or password)." };
  }

  return {
    ok: true,
    payload: {
      url: urlResult.url,
      token: auth.token,
      password: auth.password,
    },
    authLabel: auth.label,
    urlSource: urlResult.source ?? "unknown",
  };
}
