import { describe, expect, it } from "vitest";
import { extensionPairingAddress, extensionServerAddress, parseExtensionPairing } from "./browser-extension-address";

describe("browser extension instance addresses", () => {
  it.each([
    ["https://navin.example/#/leads?chat=private", "https://navin.example"],
    ["http://localhost:5173/#/leads?chat=websocket%3Aprivate", "http://localhost:5173"],
    ["http://127.0.0.1:60759/#/?bootstrapSecret=private", "http://127.0.0.1:60759"],
    ["http://localhost:8766/#/career", "http://localhost:8766"],
    ["http://[::1]:60759/#/leads", "http://[::1]:60759"],
    ["https://navin.example:9443/#/leads", "https://navin.example:9443"],
  ])("preserves the actual server and port, without session data: %s", (page, address) => {
    expect(extensionPairingAddress(page)).toBe(address);
    expect(parseExtensionPairing(JSON.stringify({ type: "navin-browser-pairing", navin_url: address, code: "ABCD 1234" })))
      .toEqual({ address, code: "ABCD 1234" });
  });
  it.each(["tauri://localhost", "https://tauri.localhost", "http://asset.localhost", "http://0.0.0.0:8766",
    "http://[::]:8766", "http://navin.example", "http://192.168.1.8:8766", "https://user:secret@navin.example",
    "file:///app/index.html", "ws://localhost:18791", "navin-host://localhost"])("rejects unreachable or unsuitable origins: %s", raw => {
    expect(() => extensionPairingAddress(raw)).toThrow();
  });
  it.each(["https://navin.example/api", "https://navin.example?token=secret", "https://navin.example/#/leads"])("rejects non-root extension API addresses: %s", raw => {
    expect(() => extensionServerAddress(raw)).toThrow();
  });
  it("does not accept an unrelated JSON paste as pairing information", () => {
    expect(() => parseExtensionPairing('{"token":"secret"}')).toThrow();
    expect(() => parseExtensionPairing('null')).toThrow();
  });
});
