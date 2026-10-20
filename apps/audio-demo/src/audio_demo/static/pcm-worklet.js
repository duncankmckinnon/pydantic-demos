// Capture mono PCM16 in 100 ms frames. The accumulator also resamples browsers
// whose AudioContext does not run at the model's requested input rate.
class PCMCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetRate = options.processorOptions.targetRate;
    this.ratio = sampleRate / this.targetRate;
    this.samples = new Int16Array(this.targetRate / 10);
    this.index = 0;
    this.sum = 0;
    this.weight = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const value of channel) {
      let remaining = 1;
      while (remaining > 1e-8) {
        const take = Math.min(remaining, this.ratio - this.weight);
        this.sum += value * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= this.ratio - 1e-8) {
          const sample = Math.max(-1, Math.min(1, this.sum / this.weight));
          this.samples[this.index++] = Math.round(sample * (sample < 0 ? 32768 : 32767));
          this.sum = 0;
          this.weight = 0;
          if (this.index === this.samples.length) {
            // Explicit little-endian wire format, independent of the host.
            const buffer = new ArrayBuffer(this.samples.length * 2);
            const view = new DataView(buffer);
            this.samples.forEach((sample, i) => view.setInt16(i * 2, sample, true));
            this.port.postMessage(buffer, [buffer]);
            this.index = 0;
          }
        }
      }
    }
    return true; // output remains silence; microphone audio is sent only to the socket
  }
}

registerProcessor("pcm-capture", PCMCapture);
