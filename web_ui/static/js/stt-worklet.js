/* Captures the microphone at the AudioContext's rate (the dictation context is
 * created at 16 kHz) and posts float32 chunks to the main thread, which forwards
 * them over the streaming-STT WebSocket. */
class SttCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.acc = [];
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (let i = 0; i < channel.length; i++) this.acc.push(channel[i]);
      if (this.acc.length >= 4096) {
        const chunk = this.acc.splice(0, 4096);
        this.port.postMessage(new Float32Array(chunk));
      }
    }
    return true;
  }
}

registerProcessor('stt-capture', SttCaptureProcessor);
