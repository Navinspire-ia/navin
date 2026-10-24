// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * End-to-end encryption of a live share, same format as navin/share/crypto.py:
 * ``base64url(nonce[12] || AES-256-GCM(json))`` with the share id as
 * associated data. The key comes from the link's #fragment and never reaches
 * a server.
 */

const NONCE_BYTES = 12;

export function b64urlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(text: string): Uint8Array<ArrayBuffer> {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

export class ShareCipher {
  private constructor(
    private readonly key: CryptoKey,
    private readonly aad: Uint8Array<ArrayBuffer>,
  ) {}

  static async create(key: string, shareId: string): Promise<ShareCipher> {
    const raw = b64urlDecode(key);
    if (raw.length !== 32) throw new Error("a share key is 32 bytes");
    const cryptoKey = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
    return new ShareCipher(cryptoKey, new Uint8Array(new TextEncoder().encode(shareId)));
  }

  async seal(payload: Record<string, unknown>): Promise<string> {
    const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
    const data = new TextEncoder().encode(JSON.stringify(payload));
    const sealed = new Uint8Array(
      await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: this.aad }, this.key, data),
    );
    const frame = new Uint8Array(NONCE_BYTES + sealed.length);
    frame.set(nonce);
    frame.set(sealed, NONCE_BYTES);
    return b64urlEncode(frame);
  }

  /** Decrypts a frame; throws on tampering or a wrong key. */
  async open(frame: string): Promise<Record<string, unknown>> {
    const raw = b64urlDecode(frame);
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: raw.subarray(0, NONCE_BYTES), additionalData: this.aad },
      this.key,
      raw.subarray(NONCE_BYTES),
    );
    const payload: unknown = JSON.parse(new TextDecoder().decode(plain));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("frame is not an object");
    }
    return payload as Record<string, unknown>;
  }
}

/** ``/s/<share_id>#<key>`` -> both parts, or null when the link is incomplete. */
export function parseShareLocation(loc: Pick<Location, "pathname" | "hash">): { shareId: string; key: string } | null {
  const match = /^\/s\/([A-Za-z0-9_-]{16,64})\/?$/.exec(loc.pathname);
  const key = loc.hash.replace(/^#/, "");
  if (!match || !/^[A-Za-z0-9_-]{43}$/.test(key)) return null;
  return { shareId: match[1], key };
}
