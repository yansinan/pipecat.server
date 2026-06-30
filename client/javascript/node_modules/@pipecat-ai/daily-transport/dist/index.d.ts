import { DailyCall, DailyCallOptions, DailyFactoryOptions, DailyMeetingSessionSummary, DailyMediaDeviceInfo } from "@daily-co/daily-js";
import { PipecatClientOptions, RTVIEventCallbacks, RTVIMessage, Tracks, Transport, TransportState } from "@pipecat-ai/client-js";
export interface DailyConnectionEndpoint {
    endpoint: string;
    headers?: Headers;
    requestData?: object;
    timeout?: number;
}
export interface DailyTransportConstructorOptions extends DailyFactoryOptions {
    bufferLocalAudioUntilBotReady?: boolean;
}
export enum DailyRTVIMessageType {
    AUDIO_BUFFERING_STARTED = "audio-buffering-started",
    AUDIO_BUFFERING_STOPPED = "audio-buffering-stopped"
}
export type DailyEventCallbacks = RTVIEventCallbacks & Partial<{
    onAudioBufferingStarted: () => void;
    onAudioBufferingStopped: () => void;
}>;
export class DailyTransport extends Transport {
    protected _callbacks: DailyEventCallbacks;
    constructor(opts?: DailyTransportConstructorOptions);
    handleUserAudioStream(data: ArrayBuffer): void;
    _sendAudioBatch(dataBatch: ArrayBuffer[]): void;
    initialize(options: PipecatClientOptions, messageHandler: (ev: RTVIMessage) => void): void;
    get dailyCallClient(): DailyCall;
    get state(): TransportState;
    private set state(value);
    getSessionInfo(): DailyMeetingSessionSummary;
    getAllCams(): Promise<DailyMediaDeviceInfo[]>;
    updateCam(camId: string): void;
    get selectedCam(): MediaDeviceInfo | Record<string, never>;
    getAllMics(): Promise<DailyMediaDeviceInfo[]>;
    updateMic(micId: string): void;
    get selectedMic(): MediaDeviceInfo | Record<string, never>;
    getAllSpeakers(): Promise<DailyMediaDeviceInfo[]>;
    updateSpeaker(speakerId: string): void;
    get selectedSpeaker(): MediaDeviceInfo | Record<string, never>;
    enableMic(enable: boolean): void;
    get isMicEnabled(): boolean;
    enableCam(enable: boolean): void;
    get isCamEnabled(): boolean;
    enableScreenShare(enable: boolean): void;
    get isSharingScreen(): boolean;
    tracks(): Tracks;
    preAuth(dailyCallOptions: DailyCallOptions): Promise<void>;
    initDevices(): Promise<void>;
    _validateConnectionParams(connectParams?: unknown): DailyCallOptions | undefined;
    _connect(connectParams?: DailyCallOptions): Promise<void>;
    sendReadyMessage(): Promise<void>;
    _disconnect(): Promise<void>;
    sendMessage(message: RTVIMessage): void;
}

//# sourceMappingURL=index.d.ts.map
