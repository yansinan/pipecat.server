import { PipecatClient, RTVIEvent } from '@pipecat-ai/client-js';
import {
  AVAILABLE_TRANSPORTS,
  DEFAULT_TRANSPORT,
  TRANSPORT_CONFIG,
  createTransport,
} from './config';

class VoiceChatClient {
  constructor() {
    this.client = null;
    this.transportType = DEFAULT_TRANSPORT;
    this.isConnected = false;

    this.setupDOM();
    this.setupEventListeners();
    this.addEvent('initialized', 'Client initialized');
  }

  setupDOM() {
    this.transportSelect = document.getElementById('transport-select');
    this.connectBtn = document.getElementById('connect-btn');
    this.micBtn = document.getElementById('mic-btn');
    this.micStatus = document.getElementById('mic-status');
    this.conversationLog = document.getElementById('conversation-log');
    this.eventsLog = document.getElementById('events-log');
    this.botVideoContainer = document.getElementById('bot-video-container');

    // Populate transport selector with available transports
    this.transportSelect.innerHTML = '';
    AVAILABLE_TRANSPORTS.forEach((transport) => {
      const option = document.createElement('option');
      option.value = transport;
      option.textContent =
        transport.charAt(0).toUpperCase() + transport.slice(1);
      if (transport === 'smallwebrtc') {
        option.textContent = 'SmallWebRTC';
      } else if (transport === 'daily') {
        option.textContent = 'Daily';
      }
      this.transportSelect.appendChild(option);
    });

    // Hide transport selector if only one transport
    if (AVAILABLE_TRANSPORTS.length === 1) {
      this.transportSelect.parentElement.style.display = 'none';
    }

    // Add placeholder message
    this.addConversationMessage(
      'Connect to start talking with your bot',
      'placeholder',
    );
  }

  setupEventListeners() {
    this.transportSelect.addEventListener('change', (e) => {
      this.transportType = e.target.value;
      this.addEvent('transport-changed', this.transportType);
    });

    this.connectBtn.addEventListener('click', () => {
      if (this.isConnected) {
        this.disconnect();
      } else {
        this.connect();
      }
    });

    this.micBtn.addEventListener('click', () => {
      if (this.client) {
        const newState = !this.client.isMicEnabled;
        this.client.enableMic(newState);
        this.updateMicButton(newState);
      }
    });

    // 浮动测试语音按钮
    const testBtn = document.getElementById('test-audio-btn');
    if (testBtn) {
      testBtn.addEventListener('click', async () => {
        if (!this.isConnected) {
          this.addEvent('error', '请先连接再发送测试语音');
          return;
        }
        testBtn.disabled = true;
        testBtn.textContent = '📤 发送中...';
        testBtn.classList.add('sending');
        try {
          // POST 到服务端 inject_test_audio — 使用与 bot start 相同的 base URL
          const botBaseUrl = (import.meta.env.VITE_BOT_START_URL || 'http://localhost:7860/start').replace('/start', '');
          const resp = await fetch(`${botBaseUrl}/inject_test_audio`, { method: 'POST' });
          const result = await resp.json();
          this.addEvent('test-audio', `已发送 ${result.bytes}B 测试语音`);
        } catch (err) {
          this.addEvent('error', `测试语音失败: ${err.message}`);
        } finally {
          testBtn.disabled = false;
          testBtn.textContent = '🎧 测试语音';
          testBtn.classList.remove('sending');
        }
      });
    }
  }

  async connect() {
    try {
      this.addEvent('connecting', `Using ${this.transportType} transport`);

      // Create transport using config
      const transport = await createTransport(this.transportType);

      // Create client
      this.client = new PipecatClient({
        transport,
        enableMic: navigator.mediaDevices?.getUserMedia !== undefined,
        enableCam: false,
        callbacks: {
          onConnected: () => {
            this.onConnected();
          },
          onDisconnected: () => {
            this.onDisconnected();
          },
          onTransportStateChanged: (state) => {
            this.addEvent('transport-state', state);
          },
          onBotReady: () => {
            this.addEvent('bot-ready', 'Bot is ready to talk');
          },
          onUserTranscript: (data) => {
            if (data.final) {
              this.addConversationMessage(data.text, 'user');
            }
          },
          onBotTranscript: (data) => {
            this.addConversationMessage(data.text, 'bot');
          },
          onError: (error) => {
            this.addEvent('error', error.message);
          },
        },
      });

      // Setup audio
      this.setupAudio();

      // Start bot and connect using config
      const connectParams = TRANSPORT_CONFIG[this.transportType];
      await this.client.startBotAndConnect(connectParams);
    } catch (error) {
      this.addEvent('error', error.message);
      console.error('Connection error:', error);
    }
  }

  async disconnect() {
    if (this.client) {
      await this.client.disconnect();
    }
  }

  setupAudio() {
    this.client.on(RTVIEvent.TrackStarted, (track, participant) => {
      if (!participant?.local) {
        if (track.kind === 'audio') {
          this.addEvent('track-started', 'Bot audio track');
          const audio = document.createElement('audio');
          audio.autoplay = true;
          audio.srcObject = new MediaStream([track]);
          document.body.appendChild(audio);
        } else if (track.kind === 'video') {
          this.addEvent('track-started', 'Bot video track');
          this.setupVideoTrack(track);
        }
      }
    });

    this.client.on(RTVIEvent.TrackStopped, (track, participant) => {
      if (!participant?.local && track.kind === 'video') {
        this.addEvent('track-stopped', 'Bot video track');
        this.clearVideoTrack();
      }
    });
  }

  /**
   * Set up a video track for display
   */
  setupVideoTrack(track) {
    // Check if we're already displaying this track
    const existingVideo = this.botVideoContainer.querySelector('video');
    if (existingVideo?.srcObject) {
      const oldTrack = existingVideo.srcObject.getVideoTracks()[0];
      if (oldTrack?.id === track.id) return;
    }

    // Clear placeholder and any existing video
    this.botVideoContainer.innerHTML = '';

    // Create video element
    const videoEl = document.createElement('video');
    videoEl.autoplay = true;
    videoEl.playsInline = true;
    videoEl.muted = true;

    // Create a new MediaStream with the track and set it as the video source
    videoEl.srcObject = new MediaStream([track]);
    this.botVideoContainer.appendChild(videoEl);
  }

  /**
   * Clear the video track and show placeholder
   */
  clearVideoTrack() {
    const video = this.botVideoContainer.querySelector('video');
    if (video?.srcObject) {
      video.srcObject.getTracks().forEach((track) => track.stop());
      video.srcObject = null;
    }
    this.botVideoContainer.innerHTML = `
      <div class="video-placeholder">
        <span>Video will appear here when connected</span>
      </div>
    `;
  }

  onConnected() {
    this.isConnected = true;
    this.connectBtn.textContent = 'Disconnect';
    this.connectBtn.classList.add('disconnect');
    this.micBtn.disabled = false;
    this.transportSelect.disabled = true;
    this.updateMicButton(this.client.isMicEnabled);
    this.addEvent('connected', 'Successfully connected to bot');

    // Clear placeholder
    if (this.conversationLog.querySelector('.placeholder')) {
      this.conversationLog.innerHTML = '';
    }
  }

  onDisconnected() {
    this.isConnected = false;
    this.connectBtn.textContent = 'Connect';
    this.connectBtn.classList.remove('disconnect');
    this.micBtn.disabled = true;
    this.transportSelect.disabled = false;
    this.updateMicButton(false);
    this.clearVideoTrack();
    this.addEvent('disconnected', 'Disconnected from bot');
  }

  updateMicButton(enabled) {
    this.micStatus.textContent = enabled ? 'Mic is On' : 'Mic is Off';
    this.micBtn.style.backgroundColor = enabled ? '#10b981' : '#1f2937';
  }

  addConversationMessage(text, role) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `conversation-message ${role}`;

    if (role === 'placeholder') {
      messageDiv.textContent = text;
    } else {
      const roleSpan = document.createElement('div');
      roleSpan.className = 'role';
      roleSpan.textContent = role === 'user' ? 'You' : 'Bot';

      const textDiv = document.createElement('div');
      textDiv.textContent = text;

      messageDiv.appendChild(roleSpan);
      messageDiv.appendChild(textDiv);
    }

    this.conversationLog.appendChild(messageDiv);
    this.conversationLog.scrollTop = this.conversationLog.scrollHeight;
  }

  addEvent(eventName, data) {
    const eventDiv = document.createElement('div');
    eventDiv.className = 'event-entry';

    const timestamp = new Date().toLocaleTimeString();
    const timestampSpan = document.createElement('span');
    timestampSpan.className = 'timestamp';
    timestampSpan.textContent = timestamp;

    const nameSpan = document.createElement('span');
    nameSpan.className = 'event-name';
    nameSpan.textContent = eventName;

    const dataSpan = document.createElement('span');
    dataSpan.className = 'event-data';
    dataSpan.textContent =
      typeof data === 'string' ? data : JSON.stringify(data);

    eventDiv.appendChild(timestampSpan);
    eventDiv.appendChild(nameSpan);
    eventDiv.appendChild(dataSpan);

    this.eventsLog.appendChild(eventDiv);
    this.eventsLog.scrollTop = this.eventsLog.scrollHeight;
  }
}

// Initialize when DOM is loaded
window.addEventListener('DOMContentLoaded', () => {
  const client = new VoiceChatClient();
  // 暴露到全局,方便 CDP 注入脚本调用
  window.__voiceClient = client;
  window.__injectTestAudio = async () => {
    const botBaseUrl = (import.meta.env.VITE_BOT_START_URL || 'http://localhost:7860/start').replace('/start', '');
    try {
      const resp = await fetch(`${botBaseUrl}/inject_test_audio`, { method: 'POST' });
      const result = await resp.json();
      client.addEvent('test-audio', `已发送 ${result.bytes}B 测试语音`);
      return result;
    } catch (err) {
      client.addEvent('error', `测试语音失败: ${err.message}`);
      throw err;
    }
  };
  // 自动连接
  setTimeout(async () => {
  client.transportType = 'smallwebrtc';
  client.transportSelect.value = 'smallwebrtc';
  client.addEvent('transport-changed', 'smallwebrtc');
  await client.connect();
  }, 500);
});
