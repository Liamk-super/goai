"use client";

import { useEffect, useRef, useState } from "react";

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
};

type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
};

type SpeechWindow = Window & {
  SpeechRecognition?: new () => SpeechRecognitionLike;
  webkitSpeechRecognition?: new () => SpeechRecognitionLike;
};

export type VoiceState = "unsupported" | "idle" | "recording" | "denied" | "error";

/** 渐进增强：仅在用户点击后请求权限；不支持时保留文本与上传路径，不显示假动画。 */
export function useVoiceCapture(onFinalText: (text: string) => void, language = "zh-CN") {
  const [state, setState] = useState<VoiceState>("idle");
  const [interim, setInterim] = useState("");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const callbackRef = useRef(onFinalText);
  callbackRef.current = onFinalText;

  useEffect(() => {
    const w = window as SpeechWindow;
    if (!w.SpeechRecognition && !w.webkitSpeechRecognition) setState("unsupported");
    return () => recognitionRef.current?.stop();
  }, []);

  function start() {
    const w = window as SpeechWindow;
    const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!Ctor) {
      setState("unsupported");
      return;
    }
    const recognition = new Ctor();
    recognition.lang = language;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = event => {
      let interimText = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) callbackRef.current(result[0].transcript);
        else interimText += result[0].transcript;
      }
      setInterim(interimText);
    };
    recognition.onerror = event => {
      setInterim("");
      setState(event.error === "not-allowed" || event.error === "service-not-allowed" ? "denied" : "error");
    };
    recognition.onend = () => {
      setInterim("");
      setState(current => (current === "recording" ? "idle" : current));
    };
    recognitionRef.current = recognition;
    setState("recording");
    recognition.start();
  }

  function stop() {
    recognitionRef.current?.stop();
    setState("idle");
    setInterim("");
  }

  return { state, interim, start, stop };
}

export const VOICE_STATUS_TEXT: Record<VoiceState, string> = {
  unsupported: "当前浏览器不支持语音输入，可继续使用文本或文件。",
  idle: "",
  recording: "录音中 · 边说边转写（中文）",
  denied: "麦克风权限被拒绝。可在浏览器设置中允许，或继续用文本输入。",
  error: "语音识别中断。可重试，或继续用文本输入。",
};
