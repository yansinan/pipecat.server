import{K as U}from"./daily-esm-DcGvl4A-.js";import{$ as q,a as y,b as k,c as R,d as z,e as V,f as x}from"./index-BIsbe3_5.js";function W(o){return o&&o.__esModule?o.default:o}function I(o,e,t,s){Object.defineProperty(o,e,{get:t,set:s,enumerable:!0,configurable:!0})}var B={};I(B,"DailyRTVIMessageType",()=>E);I(B,"DailyTransport",()=>_);class p{static floatTo16BitPCM(e){const t=new ArrayBuffer(e.length*2),s=new DataView(t);let a=0;for(let i=0;i<e.length;i++,a+=2){let r=Math.max(-1,Math.min(1,e[i]));s.setInt16(a,r<0?r*32768:r*32767,!0)}return t}static mergeBuffers(e,t){const s=new Uint8Array(e.byteLength+t.byteLength);return s.set(new Uint8Array(e),0),s.set(new Uint8Array(t),e.byteLength),s.buffer}_packData(e,t){return[new Uint8Array([t,t>>8]),new Uint8Array([t,t>>8,t>>16,t>>24])][e]}pack(e,t){if(t?.bitsPerSample)if(t?.channels){if(!t?.data)throw new Error('Missing "data"')}else throw new Error('Missing "channels"');else throw new Error('Missing "bitsPerSample"');const{bitsPerSample:s,channels:a,data:i}=t,r=["RIFF",this._packData(1,52),"WAVE","fmt ",this._packData(1,16),this._packData(0,1),this._packData(0,a.length),this._packData(1,e),this._packData(1,e*a.length*s/8),this._packData(0,a.length*s/8),this._packData(0,s),"data",this._packData(1,a[0].length*a.length*s/8),i],n=new Blob(r,{type:"audio/mpeg"}),l=URL.createObjectURL(n);return{blob:n,url:l,channelCount:a.length,sampleRate:e,duration:i.byteLength/(a.length*e*2)}}}globalThis.WavPacker=p;const $=[4186.01,4434.92,4698.63,4978.03,5274.04,5587.65,5919.91,6271.93,6644.88,7040,7458.62,7902.13],j=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"],w=[],D=[];for(let o=1;o<=8;o++)for(let e=0;e<$.length;e++){const t=$[e];w.push(t/Math.pow(2,8-o)),D.push(j[e]+o)}const C=[32,2e3],M=w.filter((o,e)=>w[e]>C[0]&&w[e]<C[1]),N=D.filter((o,e)=>w[e]>C[0]&&w[e]<C[1]);class v{static getFrequencies(e,t,s,a="frequency",i=-100,r=-30){s||(s=new Float32Array(e.frequencyBinCount),e.getFloatFrequencyData(s));const n=t/2,l=1/s.length*n;let h,c,d;if(a==="music"||a==="voice"){const f=a==="voice"?M:w,m=Array(f.length).fill(i);for(let S=0;S<s.length;S++){const O=S*l,F=s[S];for(let A=f.length-1;A>=0;A--)if(O>f[A]){m[A]=Math.max(m[A],F);break}}h=m,c=a==="voice"?M:w,d=a==="voice"?N:D}else h=Array.from(s),c=h.map((f,m)=>l*m),d=c.map(f=>`${f.toFixed(2)} Hz`);const u=h.map(f=>Math.max(0,Math.min((f-i)/(r-i),1)));return{values:new Float32Array(u),frequencies:c,labels:d}}constructor(e,t=null){if(this.fftResults=[],t){const{length:s,sampleRate:a}=t,i=new OfflineAudioContext({length:s,sampleRate:a}),r=i.createBufferSource();r.buffer=t;const n=i.createAnalyser();n.fftSize=8192,n.smoothingTimeConstant=.1,r.connect(n);const l=1/60,h=s/a,c=d=>{const u=l*d;u<h&&i.suspend(u).then(()=>{const g=new Float32Array(n.frequencyBinCount);n.getFloatFrequencyData(g),this.fftResults.push(g),c(d+1)}),d===1?i.startRendering():i.resume()};r.start(0),c(1),this.audio=e,this.context=i,this.analyser=n,this.sampleRate=a,this.audioBuffer=t}else{const s=new AudioContext,a=s.createMediaElementSource(e),i=s.createAnalyser();i.fftSize=8192,i.smoothingTimeConstant=.1,a.connect(i),i.connect(s.destination),this.audio=e,this.context=s,this.analyser=i,this.sampleRate=this.context.sampleRate,this.audioBuffer=null}}getFrequencies(e="frequency",t=-100,s=-30){let a=null;if(this.audioBuffer&&this.fftResults.length){const i=this.audio.currentTime/this.audio.duration,r=Math.min(i*this.fftResults.length|0,this.fftResults.length-1);a=this.fftResults[r]}return v.getFrequencies(this.analyser,this.sampleRate,a,e,t,s)}async resumeIfSuspended(){return this.context.state==="suspended"&&await this.context.resume(),!0}}globalThis.AudioAnalysis=v;const Q=`
class StreamProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.hasStarted = false;
    this.hasInterrupted = false;
    this.outputBuffers = [];
    this.bufferLength = 128;
    this.write = { buffer: new Float32Array(this.bufferLength), trackId: null };
    this.writeOffset = 0;
    this.trackSampleOffsets = {};
    this.port.onmessage = (event) => {
      if (event.data) {
        const payload = event.data;
        if (payload.event === 'write') {
          const int16Array = payload.buffer;
          const float32Array = new Float32Array(int16Array.length);
          for (let i = 0; i < int16Array.length; i++) {
            float32Array[i] = int16Array[i] / 0x8000; // Convert Int16 to Float32
          }
          this.writeData(float32Array, payload.trackId);
        } else if (
          payload.event === 'offset' ||
          payload.event === 'interrupt'
        ) {
          const requestId = payload.requestId;
          const trackId = this.write.trackId;
          const offset = this.trackSampleOffsets[trackId] || 0;
          this.port.postMessage({
            event: 'offset',
            requestId,
            trackId,
            offset,
          });
          if (payload.event === 'interrupt') {
            this.hasInterrupted = true;
          }
        } else {
          throw new Error(\`Unhandled event "\${payload.event}"\`);
        }
      }
    };
  }

  writeData(float32Array, trackId = null) {
    let { buffer } = this.write;
    let offset = this.writeOffset;
    for (let i = 0; i < float32Array.length; i++) {
      buffer[offset++] = float32Array[i];
      if (offset >= buffer.length) {
        this.outputBuffers.push(this.write);
        this.write = { buffer: new Float32Array(this.bufferLength), trackId };
        buffer = this.write.buffer;
        offset = 0;
      }
    }
    this.writeOffset = offset;
    return true;
  }

  process(inputs, outputs, parameters) {
    const output = outputs[0];
    const outputChannelData = output[0];
    const outputBuffers = this.outputBuffers;
    if (this.hasInterrupted) {
      this.port.postMessage({ event: 'stop' });
      return false;
    } else if (outputBuffers.length) {
      this.hasStarted = true;
      const { buffer, trackId } = outputBuffers.shift();
      for (let i = 0; i < outputChannelData.length; i++) {
        outputChannelData[i] = buffer[i] || 0;
      }
      if (trackId) {
        this.trackSampleOffsets[trackId] =
          this.trackSampleOffsets[trackId] || 0;
        this.trackSampleOffsets[trackId] += buffer.length;
      }
      return true;
    } else if (this.hasStarted) {
      this.port.postMessage({ event: 'stop' });
      return false;
    } else {
      return true;
    }
  }
}

registerProcessor('stream_processor', StreamProcessor);
`,H=new Blob([Q],{type:"application/javascript"}),G=URL.createObjectURL(H),J=G;class K{constructor({sampleRate:e=44100}={}){this.scriptSrc=J,this.sampleRate=e,this.context=null,this.stream=null,this.analyser=null,this.trackSampleOffsets={},this.interruptedTrackIds={}}async connect(){this.context=new AudioContext({sampleRate:this.sampleRate}),this._speakerID&&this.context.setSinkId(this._speakerID),this.context.state==="suspended"&&await this.context.resume();try{await this.context.audioWorklet.addModule(this.scriptSrc)}catch(t){throw console.error(t),new Error(`Could not add audioWorklet module: ${this.scriptSrc}`)}const e=this.context.createAnalyser();return e.fftSize=8192,e.smoothingTimeConstant=.1,this.analyser=e,!0}getFrequencies(e="frequency",t=-100,s=-30){if(!this.analyser)throw new Error("Not connected, please call .connect() first");return v.getFrequencies(this.analyser,this.sampleRate,null,e,t,s)}async updateSpeaker(e){const t=this._speakerID;if(this._speakerID=e,this.context)try{e==="default"?await this.context.setSinkId():await this.context.setSinkId(e)}catch(s){console.error(`Could not set sinkId to ${e}: ${s}`),this._speakerID=t}}_start(){const e=new AudioWorkletNode(this.context,"stream_processor");return e.connect(this.context.destination),e.port.onmessage=t=>{const{event:s}=t.data;if(s==="stop")e.disconnect(),this.stream=null;else if(s==="offset"){const{requestId:a,trackId:i,offset:r}=t.data,n=r/this.sampleRate;this.trackSampleOffsets[a]={trackId:i,offset:r,currentTime:n}}},this.analyser.disconnect(),e.connect(this.analyser),this.stream=e,!0}add16BitPCM(e,t="default"){if(typeof t!="string")throw new Error("trackId must be a string");if(this.interruptedTrackIds[t])return;this.stream||this._start();let s;if(e instanceof Int16Array)s=e;else if(e instanceof ArrayBuffer)s=new Int16Array(e);else throw new Error("argument must be Int16Array or ArrayBuffer");return this.stream.port.postMessage({event:"write",buffer:s,trackId:t}),s}async getTrackSampleOffset(e=!1){if(!this.stream)return null;const t=crypto.randomUUID();this.stream.port.postMessage({event:e?"interrupt":"offset",requestId:t});let s;for(;!s;)s=this.trackSampleOffsets[t],await new Promise(i=>setTimeout(()=>i(),1));const{trackId:a}=s;return e&&a&&(this.interruptedTrackIds[a]=!0),s}async interrupt(){return this.getTrackSampleOffset(!0)}}globalThis.WavStreamPlayer=K;const Z=`
class AudioProcessor extends AudioWorkletProcessor {

  constructor() {
    super();
    this.port.onmessage = this.receive.bind(this);
    this.initialize();
  }

  initialize() {
    this.foundAudio = false;
    this.recording = false;
    this.chunks = [];
  }

  /**
   * Concatenates sampled chunks into channels
   * Format is chunk[Left[], Right[]]
   */
  readChannelData(chunks, channel = -1, maxChannels = 9) {
    let channelLimit;
    if (channel !== -1) {
      if (chunks[0] && chunks[0].length - 1 < channel) {
        throw new Error(
          \`Channel \${channel} out of range: max \${chunks[0].length}\`
        );
      }
      channelLimit = channel + 1;
    } else {
      channel = 0;
      channelLimit = Math.min(chunks[0] ? chunks[0].length : 1, maxChannels);
    }
    const channels = [];
    for (let n = channel; n < channelLimit; n++) {
      const length = chunks.reduce((sum, chunk) => {
        return sum + chunk[n].length;
      }, 0);
      const buffers = chunks.map((chunk) => chunk[n]);
      const result = new Float32Array(length);
      let offset = 0;
      for (let i = 0; i < buffers.length; i++) {
        result.set(buffers[i], offset);
        offset += buffers[i].length;
      }
      channels[n] = result;
    }
    return channels;
  }

  /**
   * Combines parallel audio data into correct format,
   * channels[Left[], Right[]] to float32Array[LRLRLRLR...]
   */
  formatAudioData(channels) {
    if (channels.length === 1) {
      // Simple case is only one channel
      const float32Array = channels[0].slice();
      const meanValues = channels[0].slice();
      return { float32Array, meanValues };
    } else {
      const float32Array = new Float32Array(
        channels[0].length * channels.length
      );
      const meanValues = new Float32Array(channels[0].length);
      for (let i = 0; i < channels[0].length; i++) {
        const offset = i * channels.length;
        let meanValue = 0;
        for (let n = 0; n < channels.length; n++) {
          float32Array[offset + n] = channels[n][i];
          meanValue += channels[n][i];
        }
        meanValues[i] = meanValue / channels.length;
      }
      return { float32Array, meanValues };
    }
  }

  /**
   * Converts 32-bit float data to 16-bit integers
   */
  floatTo16BitPCM(float32Array) {
    const buffer = new ArrayBuffer(float32Array.length * 2);
    const view = new DataView(buffer);
    let offset = 0;
    for (let i = 0; i < float32Array.length; i++, offset += 2) {
      let s = Math.max(-1, Math.min(1, float32Array[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
  }

  /**
   * Retrieves the most recent amplitude values from the audio stream
   * @param {number} channel
   */
  getValues(channel = -1) {
    const channels = this.readChannelData(this.chunks, channel);
    const { meanValues } = this.formatAudioData(channels);
    return { meanValues, channels };
  }

  /**
   * Exports chunks as an audio/wav file
   */
  export() {
    const channels = this.readChannelData(this.chunks);
    const { float32Array, meanValues } = this.formatAudioData(channels);
    const audioData = this.floatTo16BitPCM(float32Array);
    return {
      meanValues: meanValues,
      audio: {
        bitsPerSample: 16,
        channels: channels,
        data: audioData,
      },
    };
  }

  receive(e) {
    const { event, id } = e.data;
    let receiptData = {};
    switch (event) {
      case 'start':
        this.recording = true;
        break;
      case 'stop':
        this.recording = false;
        break;
      case 'clear':
        this.initialize();
        break;
      case 'export':
        receiptData = this.export();
        break;
      case 'read':
        receiptData = this.getValues();
        break;
      default:
        break;
    }
    // Always send back receipt
    this.port.postMessage({ event: 'receipt', id, data: receiptData });
  }

  sendChunk(chunk) {
    const channels = this.readChannelData([chunk]);
    const { float32Array, meanValues } = this.formatAudioData(channels);
    const rawAudioData = this.floatTo16BitPCM(float32Array);
    const monoAudioData = this.floatTo16BitPCM(meanValues);
    this.port.postMessage({
      event: 'chunk',
      data: {
        mono: monoAudioData,
        raw: rawAudioData,
      },
    });
  }

  process(inputList, outputList, parameters) {
    // Copy input to output (e.g. speakers)
    // Note that this creates choppy sounds with Mac products
    const sourceLimit = Math.min(inputList.length, outputList.length);
    for (let inputNum = 0; inputNum < sourceLimit; inputNum++) {
      const input = inputList[inputNum];
      const output = outputList[inputNum];
      const channelCount = Math.min(input.length, output.length);
      for (let channelNum = 0; channelNum < channelCount; channelNum++) {
        input[channelNum].forEach((sample, i) => {
          output[channelNum][i] = sample;
        });
      }
    }
    const inputs = inputList[0];
    // There's latency at the beginning of a stream before recording starts
    // Make sure we actually receive audio data before we start storing chunks
    let sliceIndex = 0;
    if (!this.foundAudio) {
      for (const channel of inputs) {
        sliceIndex = 0; // reset for each channel
        if (this.foundAudio) {
          break;
        }
        if (channel) {
          for (const value of channel) {
            if (value !== 0) {
              // find only one non-zero entry in any channel
              this.foundAudio = true;
              break;
            } else {
              sliceIndex++;
            }
          }
        }
      }
    }
    if (inputs && inputs[0] && this.foundAudio && this.recording) {
      // We need to copy the TypedArray, because the \`process\`
      // internals will reuse the same buffer to hold each input
      const chunk = inputs.map((input) => input.slice(sliceIndex));
      this.chunks.push(chunk);
      this.sendChunk(chunk);
    }
    return true;
  }
}

registerProcessor('audio_processor', AudioProcessor);
`,X=new Blob([Z],{type:"application/javascript"}),Y=URL.createObjectURL(X),L=Y;class ee{constructor({sampleRate:e=44100,outputToSpeakers:t=!1,debug:s=!1}={}){this.scriptSrc=L,this.sampleRate=e,this.outputToSpeakers=t,this.debug=!!s,this._deviceChangeCallback=null,this._deviceErrorCallback=null,this._devices=[],this.deviceSelection=null,this.stream=null,this.processor=null,this.source=null,this.node=null,this.recording=!1,this._lastEventId=0,this.eventReceipts={},this.eventTimeout=5e3,this._chunkProcessor=()=>{},this._chunkProcessorSize=void 0,this._chunkProcessorBuffer={raw:new ArrayBuffer(0),mono:new ArrayBuffer(0)}}static async decode(e,t=44100,s=-1){const a=new AudioContext({sampleRate:t});let i,r;if(e instanceof Blob){if(s!==-1)throw new Error('Can not specify "fromSampleRate" when reading from Blob');r=e,i=await r.arrayBuffer()}else if(e instanceof ArrayBuffer){if(s!==-1)throw new Error('Can not specify "fromSampleRate" when reading from ArrayBuffer');i=e,r=new Blob([i],{type:"audio/wav"})}else{let c,d;if(e instanceof Int16Array){d=e,c=new Float32Array(e.length);for(let m=0;m<e.length;m++)c[m]=e[m]/32768}else if(e instanceof Float32Array)c=e;else if(e instanceof Array)c=new Float32Array(e);else throw new Error('"audioData" must be one of: Blob, Float32Arrray, Int16Array, ArrayBuffer, Array<number>');if(s===-1)throw new Error('Must specify "fromSampleRate" when reading from Float32Array, In16Array or Array');if(s<3e3)throw new Error('Minimum "fromSampleRate" is 3000 (3kHz)');d||(d=p.floatTo16BitPCM(c));const u={bitsPerSample:16,channels:[c],data:d};r=new p().pack(s,u).blob,i=await r.arrayBuffer()}const n=await a.decodeAudioData(i),l=n.getChannelData(0),h=URL.createObjectURL(r);return{blob:r,url:h,values:l,audioBuffer:n}}log(){return this.debug&&this.log(...arguments),!0}getSampleRate(){return this.sampleRate}getStatus(){return this.processor?this.recording?"recording":"paused":"ended"}async _event(e,t={},s=null){if(s=s||this.processor,!s)throw new Error("Can not send events without recording first");const a={event:e,id:this._lastEventId++,data:t};s.port.postMessage(a);const i=new Date().valueOf();for(;!this.eventReceipts[a.id];){if(new Date().valueOf()-i>this.eventTimeout)throw new Error(`Timeout waiting for "${e}" event`);await new Promise(n=>setTimeout(()=>n(!0),1))}const r=this.eventReceipts[a.id];return delete this.eventReceipts[a.id],r}listenForDeviceChange(e){if(e===null&&this._deviceChangeCallback)navigator.mediaDevices.removeEventListener("devicechange",this._deviceChangeCallback),this._deviceChangeCallback=null;else if(e!==null){let t=0,s=[];const a=r=>r.map(n=>n.deviceId).sort().join(","),i=async()=>{let r=++t;const n=await this.listDevices();r===t&&a(s)!==a(n)&&(s=n,e(n.slice()))};navigator.mediaDevices.addEventListener("devicechange",i),i(),this._deviceChangeCallback=i}return!0}listenForDeviceErrors(e){this._deviceErrorCallback=e}async requestPermission(){const e=await navigator.permissions.query({name:"microphone"});if(e.state==="denied")this._deviceErrorCallback&&this._deviceErrorCallback({devices:["mic"],type:"unknown",error:new Error("Microphone access denied")});else if(e.state==="prompt")try{(await navigator.mediaDevices.getUserMedia({audio:!0})).getTracks().forEach(a=>a.stop())}catch(t){console.error("Error accessing microphone."),this._deviceErrorCallback&&this._deviceErrorCallback({devices:["mic"],type:"unknown",error:t})}return!0}async listDevices(){if(!navigator.mediaDevices||!("enumerateDevices"in navigator.mediaDevices))throw new Error("Could not request user devices");return await this.requestPermission(),(await navigator.mediaDevices.enumerateDevices()).filter(s=>s.kind==="audioinput")}async begin(e){if(this.processor)throw new Error("Already connected: please call .end() to start a new session");if(!navigator.mediaDevices||!("getUserMedia"in navigator.mediaDevices))throw this._deviceErrorCallback&&this._deviceErrorCallback({devices:["mic","cam"],type:"undefined-mediadevices"}),new Error("Could not request user media");e=e??this.deviceSelection?.deviceId;try{const n={audio:!0};e&&(n.audio={deviceId:{exact:e}}),this.stream=await navigator.mediaDevices.getUserMedia(n)}catch(n){throw this._deviceErrorCallback&&this._deviceErrorCallback({devices:["mic"],type:"unknown",error:n}),new Error("Could not start media stream")}this.listDevices().then(n=>{e=this.stream.getAudioTracks()[0].getSettings().deviceId,console.log("find current device",n,e,this.stream.getAudioTracks()[0].getSettings()),this.deviceSelection=n.find(l=>l.deviceId===e),console.log("current device",this.deviceSelection)});const t=new AudioContext({sampleRate:this.sampleRate}),s=t.createMediaStreamSource(this.stream);try{await t.audioWorklet.addModule(this.scriptSrc)}catch(n){throw console.error(n),new Error(`Could not add audioWorklet module: ${this.scriptSrc}`)}const a=new AudioWorkletNode(t,"audio_processor");a.port.onmessage=n=>{const{event:l,id:h,data:c}=n.data;if(l==="receipt")this.eventReceipts[h]=c;else if(l==="chunk")if(this._chunkProcessorSize){const d=this._chunkProcessorBuffer;this._chunkProcessorBuffer={raw:p.mergeBuffers(d.raw,c.raw),mono:p.mergeBuffers(d.mono,c.mono)},this._chunkProcessorBuffer.mono.byteLength>=this._chunkProcessorSize&&(this._chunkProcessor(this._chunkProcessorBuffer),this._chunkProcessorBuffer={raw:new ArrayBuffer(0),mono:new ArrayBuffer(0)})}else this._chunkProcessor(c)};const i=s.connect(a),r=t.createAnalyser();return r.fftSize=8192,r.smoothingTimeConstant=.1,i.connect(r),this.outputToSpeakers&&(console.warn(`Warning: Output to speakers may affect sound quality,
especially due to system audio feedback preventative measures.
use only for debugging`),r.connect(t.destination)),this.source=s,this.node=i,this.analyser=r,this.processor=a,console.log("begin completed"),!0}getFrequencies(e="frequency",t=-100,s=-30){if(!this.processor)throw new Error("Session ended: please call .begin() first");return v.getFrequencies(this.analyser,this.sampleRate,null,e,t,s)}async pause(){if(this.processor){if(!this.recording)throw new Error("Already paused: please call .record() first")}else throw new Error("Session ended: please call .begin() first");return this._chunkProcessorBuffer.raw.byteLength&&this._chunkProcessor(this._chunkProcessorBuffer),this.log("Pausing ..."),await this._event("stop"),this.recording=!1,!0}async record(e=()=>{},t=8192){if(this.processor){if(this.recording)throw new Error("Already recording: please call .pause() first");if(typeof e!="function")throw new Error("chunkProcessor must be a function")}else throw new Error("Session ended: please call .begin() first");return this._chunkProcessor=e,this._chunkProcessorSize=t,this._chunkProcessorBuffer={raw:new ArrayBuffer(0),mono:new ArrayBuffer(0)},this.log("Recording ..."),await this._event("start"),this.recording=!0,!0}async clear(){if(!this.processor)throw new Error("Session ended: please call .begin() first");return await this._event("clear"),!0}async read(){if(!this.processor)throw new Error("Session ended: please call .begin() first");return this.log("Reading ..."),await this._event("read")}async save(e=!1){if(!this.processor)throw new Error("Session ended: please call .begin() first");if(!e&&this.recording)throw new Error("Currently recording: please call .pause() first, or call .save(true) to force");this.log("Exporting ...");const t=await this._event("export");return new p().pack(this.sampleRate,t.audio)}async end(){if(!this.processor)throw new Error("Session ended: please call .begin() first");const e=this.processor;this.log("Stopping ..."),await this._event("stop"),this.recording=!1,this.stream.getTracks().forEach(r=>r.stop()),this.log("Exporting ...");const s=await this._event("export",{},e);return this.processor.disconnect(),this.source.disconnect(),this.node.disconnect(),this.analyser.disconnect(),this.stream=null,this.processor=null,this.source=null,this.node=null,new p().pack(this.sampleRate,s.audio)}async quit(){return this.listenForDeviceChange(null),this.deviceSelection=null,this.processor&&await this.end(),!0}}globalThis.WavRecorder=ee;function P(o,e,t){if(e===t)return o;const s=new Int16Array(o),a=e/t,i=Math.round(s.length/a),r=new ArrayBuffer(i*2),n=new Int16Array(r);for(let l=0;l<i;l++){const h=l*a,c=Math.floor(h),d=Math.min(c+1,s.length-1),u=h-c;n[l]=Math.round(s[c]*(1-u)+s[d]*u)}return r}class te{constructor({sampleRate:e=44100,outputToSpeakers:t=!1,debug:s=!1}={}){this.scriptSrc=L,this.sampleRate=e,this.outputToSpeakers=t,this.debug=!!s,this.stream=null,this.processor=null,this.source=null,this.node=null,this.recording=!1,this._lastEventId=0,this.eventReceipts={},this.eventTimeout=5e3,this._chunkProcessor=()=>{},this._chunkProcessorSize=void 0,this._chunkProcessorBuffer={raw:new ArrayBuffer(0),mono:new ArrayBuffer(0)}}log(){return this.debug&&this.log(...arguments),!0}getSampleRate(){return this.sampleRate}getStatus(){return this.processor?this.recording?"recording":"paused":"ended"}async _event(e,t={},s=null){if(s=s||this.processor,!s)throw new Error("Can not send events without recording first");const a={event:e,id:this._lastEventId++,data:t};s.port.postMessage(a);const i=new Date().valueOf();for(;!this.eventReceipts[a.id];){if(new Date().valueOf()-i>this.eventTimeout)throw new Error(`Timeout waiting for "${e}" event`);await new Promise(n=>setTimeout(()=>n(!0),1))}const r=this.eventReceipts[a.id];return delete this.eventReceipts[a.id],r}async begin(e){if(this.processor)throw new Error("Already connected: please call .end() to start a new session");if(!e||e.kind!=="audio")throw new Error("No audio track provided");this.stream=new MediaStream([e]);const t=navigator.userAgent.toLowerCase().includes("firefox");let s;t?s=new AudioContext:s=new AudioContext({sampleRate:this.sampleRate});const a=s.sampleRate,i=s.createMediaStreamSource(this.stream);try{await s.audioWorklet.addModule(this.scriptSrc)}catch(h){throw console.error(h),new Error(`Could not add audioWorklet module: ${this.scriptSrc}`)}const r=new AudioWorkletNode(s,"audio_processor");r.port.onmessage=h=>{const{event:c,id:d,data:u}=h.data;if(c==="receipt")this.eventReceipts[d]=u;else if(c==="chunk"){const g={raw:P(u.raw,a,this.sampleRate),mono:P(u.mono,a,this.sampleRate)};if(this._chunkProcessorSize){const f=this._chunkProcessorBuffer;this._chunkProcessorBuffer={raw:p.mergeBuffers(f.raw,g.raw),mono:p.mergeBuffers(f.mono,g.mono)},this._chunkProcessorBuffer.mono.byteLength>=this._chunkProcessorSize&&(this._chunkProcessor(this._chunkProcessorBuffer),this._chunkProcessorBuffer={raw:new ArrayBuffer(0),mono:new ArrayBuffer(0)})}else this._chunkProcessor(g)}};const n=i.connect(r),l=s.createAnalyser();return l.fftSize=8192,l.smoothingTimeConstant=.1,n.connect(l),this.outputToSpeakers&&(console.warn(`Warning: Output to speakers may affect sound quality,
especially due to system audio feedback preventative measures.
use only for debugging`),l.connect(s.destination)),this.source=i,this.node=n,this.analyser=l,this.processor=r,!0}getFrequencies(e="frequency",t=-100,s=-30){if(!this.processor)throw new Error("Session ended: please call .begin() first");return v.getFrequencies(this.analyser,this.sampleRate,null,e,t,s)}async pause(){if(this.processor){if(!this.recording)throw new Error("Already paused: please call .record() first")}else throw new Error("Session ended: please call .begin() first");return this._chunkProcessorBuffer.raw.byteLength&&this._chunkProcessor(this._chunkProcessorBuffer),this.log("Pausing ..."),await this._event("stop"),this.recording=!1,!0}async record(e=()=>{},t=8192){if(this.processor){if(this.recording)throw new Error("Already recording: HELLO please call .pause() first");if(typeof e!="function")throw new Error("chunkProcessor must be a function")}else throw new Error("Session ended: please call .begin() first");return this._chunkProcessor=e,this._chunkProcessorSize=t,this._chunkProcessorBuffer={raw:new ArrayBuffer(0),mono:new ArrayBuffer(0)},this.log("Recording ..."),await this._event("start"),this.recording=!0,!0}async clear(){if(!this.processor)throw new Error("Session ended: please call .begin() first");return await this._event("clear"),!0}async read(){if(!this.processor)throw new Error("Session ended: please call .begin() first");return this.log("Reading ..."),await this._event("read")}async save(e=!1){if(!this.processor)throw new Error("Session ended: please call .begin() first");if(!e&&this.recording)throw new Error("Currently recording: please call .pause() first, or call .save(true) to force");this.log("Exporting ...");const t=await this._event("export");return new p().pack(this.sampleRate,t.audio)}async end(){if(!this.processor)throw new Error("Session ended: please call .begin() first");const e=this.processor;this.log("Stopping ..."),await this._event("stop"),this.recording=!1,this.log("Exporting ...");const t=await this._event("export",{},e);return this.processor.disconnect(),this.source.disconnect(),this.node.disconnect(),this.analyser.disconnect(),this.stream=null,this.processor=null,this.source=null,this.node=null,new p().pack(this.sampleRate,t.audio)}async quit(){return this.listenForDeviceChange(null),this.processor&&await this.end(),!0}}globalThis.WavRecorder=WavRecorder;var T={};T=JSON.parse('{"name":"@pipecat-ai/daily-transport","version":"1.6.5","license":"BSD-2-Clause","main":"dist/index.js","module":"dist/index.module.js","types":"dist/index.d.ts","source":"src/index.ts","repository":{"type":"git","url":"git+https://github.com/pipecat-ai/pipecat-client-web-transports.git"},"exports":{".":{"types":"./dist/index.d.ts","import":"./dist/index.module.js","require":"./dist/index.js"}},"files":["dist","package.json","README.md"],"scripts":{"build":"parcel build --no-cache","dev":"parcel watch","lint":"eslint . --ext ts --report-unused-disable-directives --max-warnings 0"},"devDependencies":{"@pipecat-ai/client-js":"^1.10.0","eslint":"9.39.1","eslint-config-prettier":"^9.1.0","eslint-plugin-simple-import-sort":"^12.1.1"},"peerDependencies":{"@pipecat-ai/client-js":"~1.10.0"},"dependencies":{"@daily-co/daily-js":"^0.90.0"},"description":"Pipecat Daily Transport Package","author":"Daily.co","bugs":{"url":"https://github.com/pipecat-ai/pipecat-client-web-transports/issues"},"homepage":"https://github.com/pipecat-ai/pipecat-client-web-transports/blob/main/transports/daily-webrtc/README.md"}');var E;(function(o){o.AUDIO_BUFFERING_STARTED="audio-buffering-started",o.AUDIO_BUFFERING_STOPPED="audio-buffering-stopped"})(E||(E={}));class se{constructor(e){this._daily=e,this._proxy=new Proxy(this._daily,{get:(t,s,a)=>{if(typeof t[s]=="function"){let i;switch(String(s)){case"preAuth":i="Calls to preAuth() are disabled. Please use Transport.preAuth()";break;case"startCamera":i="Calls to startCamera() are disabled. Please use PipecatClient.initDevices()";break;case"join":i="Calls to join() are disabled. Please use PipecatClient.connect()";break;case"leave":i="Calls to leave() are disabled. Please use PipecatClient.disconnect()";break;case"destroy":i="Calls to destroy() are disabled.";break}return i?()=>{throw new Error(i)}:(...r)=>t[s](...r)}return Reflect.get(t,s,a)}})}get proxy(){return this._proxy}}class _ extends q{constructor(e={}){super(),this._botId="",this._selectedCam={},this._selectedMic={},this._selectedSpeaker={},this._currentAudioTrack=null,this._audioQueue=[],this._callbacks={};const{bufferLocalAudioUntilBotReady:t,...s}=e;this._dailyFactoryOptions=s,typeof this._dailyFactoryOptions.dailyConfig?.useDevicePreferenceCookies>"u"&&(this._dailyFactoryOptions.dailyConfig==null&&(this._dailyFactoryOptions.dailyConfig={}),this._dailyFactoryOptions.dailyConfig.useDevicePreferenceCookies=!0),this._bufferLocalAudioUntilBotReady=t||!1,this._daily=U.createCallObject({...this._dailyFactoryOptions,allowMultipleCallInstances:!0}),this._dailyWrapper=new se(this._daily)}setupRecorder(){this._mediaStreamRecorder=new te({sampleRate:_.RECORDER_SAMPLE_RATE})}handleUserAudioStream(e){this._audioQueue.push(e)}flushAudioQueue(){if(this._audioQueue.length!==0)for(y.debug(`Will flush audio queue: ${this._audioQueue.length}`);this._audioQueue.length>0;){const t=[];for(;t.length<10&&this._audioQueue.length>0;){const s=this._audioQueue.shift();s&&t.push(s)}t.length>0&&this._sendAudioBatch(t)}}_sendAudioBatch(e){const s={id:"raw-audio-batch",label:"rtvi-ai",type:"raw-audio-batch",data:{base64AudioBatch:e.map(a=>{const i=new Uint8Array(a);return btoa(String.fromCharCode(...i))}),sampleRate:_.RECORDER_SAMPLE_RATE,numChannels:1}};this.sendMessage(s)}initialize(e,t){this._bufferLocalAudioUntilBotReady&&this.setupRecorder(),this._callbacks=e.callbacks??{},this._onMessage=t,(this._dailyFactoryOptions.startVideoOff==null||e.enableCam!=null)&&(this._dailyFactoryOptions.startVideoOff=!(e.enableCam??!1)),(this._dailyFactoryOptions.startAudioOff==null||e.enableMic!=null)&&(this._dailyFactoryOptions.startAudioOff=!(e.enableMic??!0)),this.attachEventListeners(),this.state="disconnected",y.debug("[Daily Transport] Initialized",W(T).version)}get dailyCallClient(){return this._dailyWrapper.proxy}get state(){return this._state}set state(e){this._state!==e&&(this._state=e,this._callbacks.onTransportStateChanged?.(e))}getSessionInfo(){return this._daily.meetingSessionSummary()}async getAllCams(){const{devices:e}=await this._daily.enumerateDevices();return e.filter(t=>t.kind==="videoinput")}updateCam(e){this._daily.setInputDevicesAsync({videoDeviceId:e}).then(t=>{this._selectedCam=t.camera})}get selectedCam(){return this._selectedCam}async getAllMics(){const{devices:e}=await this._daily.enumerateDevices();return e.filter(t=>t.kind==="audioinput")}updateMic(e){this._daily.setInputDevicesAsync({audioDeviceId:e}).then(t=>{this._selectedMic=t.mic})}get selectedMic(){return this._selectedMic}async getAllSpeakers(){const{devices:e}=await this._daily.enumerateDevices();return e.filter(t=>t.kind==="audiooutput")}updateSpeaker(e){this._daily.setOutputDeviceAsync({outputDeviceId:e}).then(t=>{this._selectedSpeaker=t.speaker}).catch(t=>{this._callbacks.onDeviceError?.(new k(["speaker"],t.type??"unknown",t.message))})}get selectedSpeaker(){return this._selectedSpeaker}enableMic(e){this._dailyFactoryOptions.startAudioOff=!e,this._daily.participants()?.local&&this._daily.setLocalAudio(e)}get isMicEnabled(){return this._daily.localAudio()}enableCam(e){this._dailyFactoryOptions.startVideoOff=!e,this._daily.participants()?.local&&this._daily.setLocalVideo(e)}get isCamEnabled(){return this._daily.localVideo()}enableScreenShare(e){e?this._daily.startScreenShare():this._daily.stopScreenShare()}get isSharingScreen(){return this._daily.localScreenAudio()||this._daily.localScreenVideo()}tracks(){const e=this._daily.participants()??{},t=e?.[this._botId],s={local:{audio:e?.local?.tracks?.audio?.persistentTrack,screenAudio:e?.local?.tracks?.screenAudio?.persistentTrack,screenVideo:e?.local?.tracks?.screenVideo?.persistentTrack,video:e?.local?.tracks?.video?.persistentTrack}};return t&&(s.bot={audio:t?.tracks?.audio?.persistentTrack,video:t?.tracks?.video?.persistentTrack}),s}async startRecording(){try{y.info("[Daily Transport] Initializing recording"),await this._mediaStreamRecorder.record(e=>{this.handleUserAudioStream(e.mono)},_.RECORDER_CHUNK_SIZE),this._callbacks.onAudioBufferingStarted?.(),y.info("[Daily Transport] Recording Initialized")}catch(e){e.message.includes("Already recording")||y.error("Error starting recording",e)}}async preAuth(e){this._dailyFactoryOptions=e,await this._daily.preAuth(e)}async initDevices(){if(!this._daily)throw new R("Transport instance not initialized");this.state="initializing";const e=await this._daily.startCamera(this._dailyFactoryOptions),{devices:t}=await this._daily.enumerateDevices(),s=t.filter(r=>r.kind==="videoinput"),a=t.filter(r=>r.kind==="audioinput"),i=t.filter(r=>r.kind==="audiooutput");this._selectedCam=e.camera,this._selectedMic=e.mic,this._selectedSpeaker=e.speaker,this._callbacks.onAvailableCamsUpdated?.(s),this._callbacks.onAvailableMicsUpdated?.(a),this._callbacks.onAvailableSpeakersUpdated?.(i),this._callbacks.onCamUpdated?.(e.camera),this._callbacks.onMicUpdated?.(e.mic),this._callbacks.onSpeakerUpdated?.(e.speaker),this._daily.isLocalAudioLevelObserverRunning()||await this._daily.startLocalAudioLevelObserver(100),this._daily.isRemoteParticipantsAudioLevelObserverRunning()||await this._daily.startRemoteParticipantsAudioLevelObserver(100),this.state="initialized"}_validateConnectionParams(e){if(e==null)return;if(typeof e!="object")throw new R("Invalid connection parameters");const t=e;return t.room_url?(t.url=t.room_url,delete t.room_url):t.dailyRoom&&(t.url=t.dailyRoom,delete t.dailyRoom),t.dailyToken&&(t.token=t.dailyToken,delete t.dailyToken),t.token||delete t.token,t}async _connect(e){if(!this._daily)throw new R("Transport instance not initialized");e&&(this._dailyFactoryOptions={...this._dailyFactoryOptions,...e}),this.state="connecting";try{await this._daily.join(this._dailyFactoryOptions)}catch(s){throw y.error("Failed to join room",s),this.state="error",new z}if(this._abortController?.signal.aborted)return;const t=await this._daily.room();this._maxMessageSize=t?.domainConfig?.max_app_message_size||10485760,this.state="connected",this._callbacks.onConnected?.()}async sendReadyMessage(){return new Promise(e=>{const t=()=>{const i=navigator.userAgent;return/iPad|iPhone|iPod/.test(i)||/Macintosh/.test(i)&&"ontouchend"in document},s=()=>{this.state="ready",this.flushAudioQueue(),this.sendMessage(x.clientReady()),this.stopRecording(),e()};for(const i in this._daily.participants()){const r=this._daily.participants()[i];if(!r.local&&r.tracks?.audio?.persistentTrack){s(),e();return}}const a=i=>{i.participant?.local||(this._daily.off("track-started",a),t()?(y.debug("[Daily Transport] iOS device detected, adding 0.5 second delay before sending ready message"),setTimeout(s,500)):s())};this._daily.on("track-started",a)})}stopRecording(){this._mediaStreamRecorder&&this._mediaStreamRecorder.getStatus()!=="ended"&&(this._mediaStreamRecorder.end(),this._callbacks.onAudioBufferingStopped?.())}attachEventListeners(){this._daily.on("available-devices-updated",this.handleAvailableDevicesUpdated.bind(this)),this._daily.on("selected-devices-updated",this.handleSelectedDevicesUpdated.bind(this)),this._daily.on("camera-error",this.handleDeviceError.bind(this)),this._daily.on("track-started",this.handleTrackStarted.bind(this)),this._daily.on("track-stopped",this.handleTrackStopped.bind(this)),this._daily.on("participant-joined",this.handleParticipantJoined.bind(this)),this._daily.on("participant-left",this.handleParticipantLeft.bind(this)),this._daily.on("local-audio-level",this.handleLocalAudioLevel.bind(this)),this._daily.on("remote-participants-audio-level",this.handleRemoteAudioLevel.bind(this)),this._daily.on("app-message",this.handleAppMessage.bind(this)),this._daily.on("left-meeting",this.handleLeftMeeting.bind(this)),this._daily.on("error",this.handleFatalError.bind(this)),this._daily.on("nonfatal-error",this.handleNonFatalError.bind(this))}async _disconnect(){this.state="disconnecting",this._daily.stopLocalAudioLevelObserver(),this._daily.stopRemoteParticipantsAudioLevelObserver(),this._audioQueue=[],this._currentAudioTrack=null,this.stopRecording(),await this._daily.leave()}sendMessage(e){try{this._daily.sendAppMessage(e,"*")}catch(t){throw t instanceof Error&&t.message.includes("Message data too large")?new V(t.message):t}}handleAppMessage(e){e.data.label==="rtvi-ai"&&this._onMessage({id:e.data.id,type:e.data.type,data:e.data.data})}handleAvailableDevicesUpdated(e){this._callbacks.onAvailableCamsUpdated?.(e.availableDevices.filter(t=>t.kind==="videoinput")),this._callbacks.onAvailableMicsUpdated?.(e.availableDevices.filter(t=>t.kind==="audioinput")),this._callbacks.onAvailableSpeakersUpdated?.(e.availableDevices.filter(t=>t.kind==="audiooutput"))}handleSelectedDevicesUpdated(e){this._selectedCam?.deviceId!==e.devices.camera&&(this._selectedCam=e.devices.camera,this._callbacks.onCamUpdated?.(e.devices.camera)),this._selectedMic?.deviceId!==e.devices.mic&&(this._selectedMic=e.devices.mic,this._callbacks.onMicUpdated?.(e.devices.mic)),this._selectedSpeaker?.deviceId!==e.devices.speaker&&(this._selectedSpeaker=e.devices.speaker,this._callbacks.onSpeakerUpdated?.(e.devices.speaker))}handleDeviceError(e){const t=s=>{const a=[];switch(s.type){case"permissions":return s.blockedMedia.forEach(i=>{a.push(i==="video"?"cam":"mic")}),new k(a,s.type,s.msg,{blockedBy:s.blockedBy});case"not-found":return s.missingMedia.forEach(i=>{a.push(i==="video"?"cam":"mic")}),new k(a,s.type,s.msg);case"constraints":return s.failedMedia.forEach(i=>{a.push(i==="video"?"cam":"mic")}),new k(a,s.type,s.msg,{reason:s.reason});case"cam-in-use":return a.push("cam"),new k(a,"in-use",s.msg);case"mic-in-use":return a.push("mic"),new k(a,"in-use",s.msg);case"cam-mic-in-use":return a.push("cam"),a.push("mic"),new k(a,"in-use",s.msg);default:return a.push("cam"),a.push("mic"),new k(a,s.type,s.msg)}};this._callbacks.onDeviceError?.(t(e.error))}async handleLocalAudioTrack(e){if(this.state=="ready"||!this._bufferLocalAudioUntilBotReady)return;switch(this._mediaStreamRecorder.getStatus()){case"ended":try{await this._mediaStreamRecorder.begin(e),await this.startRecording()}catch{}break;case"paused":await this.startRecording();break;default:if(this._currentAudioTrack!==e)try{await this._mediaStreamRecorder.end(),await this._mediaStreamRecorder.begin(e),await this.startRecording()}catch{}else y.warn("track-started event received for current track and already recording");break}this._currentAudioTrack=e}handleTrackStarted(e){e.type==="screenAudio"||e.type==="screenVideo"?this._callbacks.onScreenTrackStarted?.(e.track,e.participant?b(e.participant):void 0):(e.participant?.local&&e.track.kind==="audio"&&this.handleLocalAudioTrack(e.track),this._callbacks.onTrackStarted?.(e.track,e.participant?b(e.participant):void 0))}handleTrackStopped(e){e.type==="screenAudio"||e.type==="screenVideo"?this._callbacks.onScreenTrackStopped?.(e.track,e.participant?b(e.participant):void 0):this._callbacks.onTrackStopped?.(e.track,e.participant?b(e.participant):void 0)}handleParticipantJoined(e){const t=b(e.participant);this._callbacks.onParticipantJoined?.(t),!t.local&&(this._botId=e.participant.session_id,this._callbacks.onBotConnected?.(t))}handleParticipantLeft(e){const t=b(e.participant);this._callbacks.onParticipantLeft?.(t),!t.local&&(this._botId="",this._callbacks.onBotDisconnected?.(t))}handleLocalAudioLevel(e){this._callbacks.onLocalAudioLevel?.(e.audioLevel)}handleRemoteAudioLevel(e){const t=this._daily.participants(),s=Object.keys(e.participantsAudioLevel);for(let a=0;a<s.length;a++){const i=s[a],r=e.participantsAudioLevel[i];this._callbacks.onRemoteAudioLevel?.(r,b(t[i]))}}handleLeftMeeting(){this.state="disconnected",this._botId="",this._callbacks.onDisconnected?.()}handleFatalError(e){y.error("Daily fatal error",e.errorMsg),this.state="error",this._botId="",this._callbacks.onError?.(x.error(e.errorMsg,!0))}handleNonFatalError(e){e.type==="screen-share-error"&&this._callbacks.onScreenShareError?.(e.errorMsg)}}_.RECORDER_SAMPLE_RATE=16e3;_.RECORDER_CHUNK_SIZE=512;const b=o=>({id:o.user_id,local:o.local,name:o.user_name});export{E as DailyRTVIMessageType,_ as DailyTransport};
