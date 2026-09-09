import { describe, expect, it } from "vitest";

import {
  deviceLabel,
  microphoneConstraints,
  microphoneErrorKind,
  resolveDeviceChoice,
  toVoiceDeviceList,
} from "./voice-devices";

const FALLBACKS = {
  input: (index: number) => `Microphone ${index}`,
  output: (index: number) => `Speaker ${index}`,
};

function info(kind: MediaDeviceKind, deviceId: string, label = ""): Pick<MediaDeviceInfo, "deviceId" | "label" | "kind"> {
  return { kind, deviceId, label };
}

describe("toVoiceDeviceList", () => {
  it("splits inputs and outputs, labels hidden devices, puts defaults first", () => {
    const list = toVoiceDeviceList(
      [
        info("audioinput", "abc", "Casque USB"),
        info("audioinput", "default", "Default - Realtek"),
        info("videoinput", "cam", "Webcam"),
        info("audiooutput", "spk", ""),
        info("audiooutput", "default", "Default speaker"),
        info("audioinput", "", "no id"),
      ],
      FALLBACKS,
      true,
    );
    expect(list.inputs.map((device) => device.deviceId)).toEqual(["default", "abc"]);
    expect(list.outputs.map((device) => device.label)).toEqual(["Default speaker", "Speaker 1"]);
    expect(list.outputSelectable).toBe(true);
  });

  it("hides speakers where the page cannot route audio", () => {
    const list = toVoiceDeviceList(
      [info("audioinput", "mic", "Mic"), info("audiooutput", "spk", "Speaker")],
      FALLBACKS,
      false,
    );
    expect(list.inputs).toHaveLength(1);
    expect(list.outputs).toEqual([]);
    expect(list.outputSelectable).toBe(false);
  });
});

describe("deviceLabel / resolveDeviceChoice", () => {
  it("falls back to a numbered label when the platform hides it", () => {
    expect(deviceLabel({ deviceId: "x", label: "  " }, 1, FALLBACKS.input)).toBe("Microphone 2");
    expect(deviceLabel({ deviceId: "x", label: "Jabra" }, 1, FALLBACKS.input)).toBe("Jabra");
  });

  it("keeps the remembered device only while it is plugged in", () => {
    const devices = [{ deviceId: "a", label: "A" }, { deviceId: "b", label: "B" }];
    expect(resolveDeviceChoice(devices, "b")).toBe("b");
    expect(resolveDeviceChoice(devices, "gone")).toBeNull();
    expect(resolveDeviceChoice(devices, null)).toBeNull();
  });
});

describe("microphoneConstraints / microphoneErrorKind", () => {
  it("asks for echo cancellation and pins the device when chosen", () => {
    const anyMic = microphoneConstraints(null).audio as MediaTrackConstraints;
    expect(anyMic.echoCancellation).toBe(true);
    expect(anyMic.deviceId).toBeUndefined();
    const pinned = microphoneConstraints("usb-1").audio as MediaTrackConstraints;
    expect(pinned.deviceId).toEqual({ exact: "usb-1" });
  });

  it("maps browser errors to the reasons the bar can explain", () => {
    const named = (name: string) => Object.assign(new Error(name), { name });
    expect(microphoneErrorKind(named("NotAllowedError"))).toBe("permission");
    expect(microphoneErrorKind(named("NotFoundError"))).toBe("noMicrophone");
    expect(microphoneErrorKind(named("OverconstrainedError"))).toBe("noMicrophone");
    expect(microphoneErrorKind(named("NotReadableError"))).toBe("busy");
    expect(microphoneErrorKind(new Error("Permission dismissed"))).toBe("permission");
    expect(microphoneErrorKind(new Error("boom"))).toBe("failed");
    expect(microphoneErrorKind("nope")).toBe("failed");
  });
});
