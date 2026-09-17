const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

for (const rate of [24000, 44100, 48000]) {
  test(`captures 100 ms PCM16 frames from ${rate} Hz microphone input`, () => {
    let Processor;
    const frames = [];
    const context = vm.createContext({
      sampleRate: rate,
      AudioWorkletProcessor: class { constructor() { this.port = { postMessage: (data) => frames.push(data) }; } },
      registerProcessor: (_, processor) => { Processor = processor; },
    });
    vm.runInContext(readFileSync(join(__dirname, "../src/audio_demo/static/pcm-worklet.js"), "utf8"), context);
    const capture = new Processor({ processorOptions: { targetRate: 24000 } });
    const input = new Float32Array(rate / 10).fill(0.5);
    for (let offset = 0; offset < input.length; offset += 128) {
      capture.process([[input.slice(offset, offset + 128)]]);
    }
    assert.equal(frames.length, 1);
    assert.equal(frames[0].byteLength, 4800);
    const pcm = new DataView(frames[0]);
    assert.equal(pcm.getInt16(0, true), 16384);
    assert.equal(pcm.getInt16(4798, true), 16384);
  });
}
