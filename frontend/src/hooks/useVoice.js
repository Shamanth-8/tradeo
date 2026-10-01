import { useCallback, useEffect, useRef, useState } from 'react'
import { aiApi } from '../services/api'
import { startLocalListener } from './localSpeech'

/**
 * Voice for Tradeo — recognition, wake word, and spoken replies.
 *
 * Local first: the microphone is recorded here and transcribed by Whisper on
 * Tradeo's own backend, and replies are spoken by the local Piper voice.
 * Nothing goes to a speech API. The browser's Web Speech engines are only
 * the fallback when the local ones aren't installed; Chrome's recogniser
 * streams audio to Google and fails with "network" when it can't reach it.
 *
 * Two modes:
 *   push-to-talk  — hold/click to speak, one question at a time
 *   ambient       — always listening for "Tradeo …", like the thing this is
 *                   obviously modelled on
 *
 * Ambient mode is opt-in and off by default: a hot mic in a room is a choice
 * the user should make deliberately, not a default they discover later.
 */

const SpeechRecognition =
    typeof window !== 'undefined' &&
    (window.SpeechRecognition || window.webkitSpeechRecognition)

export const voiceSupported =
    Boolean(SpeechRecognition) ||
    (typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia))
export const speechSupported =
    typeof window !== 'undefined' && 'speechSynthesis' in window

// How recognisers spell the wake word ("Tradio", "Trade O"…).
function wakeRegex(word) {
    const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const variants = word.toLowerCase() === 'tradeo'
        ? [escaped, 'tradio', 'trade[\\s-]?o', 'trudeau', 'trade[\\s-]?yo']
        : [escaped]
    return new RegExp(`\\b(?:${variants.join('|')})\\b[\\s,.:!?-]*`, 'i')
}

// Recognisers return interim guesses constantly. Waiting for a pause avoids
// firing a question at the model mid-sentence.
const SILENCE_MS = 1100

export function useVoice({
    wakeWord = 'tradeo',
    sessionId = 'voice',
    onAnswer,
    onTranscript,
    onNavigate,
    autoSpeak = true,
} = {}) {
    const [listening, setListening] = useState(false)
    const [ambient, setAmbient] = useState(false)
    const [speaking, setSpeaking] = useState(false)
    const [thinking, setThinking] = useState(false)
    const [transcript, setTranscript] = useState('')
    const [interim, setInterim] = useState('')
    const [error, setError] = useState(null)
    const [amplitude, setAmplitude] = useState(0)

    const recognitionRef = useRef(null)
    const silenceTimer = useRef(null)
    const finalText = useRef('')
    const ambientRef = useRef(false)
    const shouldRestart = useRef(false)
    const audioCleanup = useRef(null)
    const utteranceRef = useRef(null)   // holds the utterance so it can't be GC'd
    const keepAlive = useRef(null)      // interval fighting Chrome's auto-pause
    const listeningRef = useRef(false)  // speak() needs this without re-creating itself
    const localRef = useRef(null)       // the local (Whisper) listener, when running
    const audioRef = useRef(null)       // the Piper reply being played
    const engineRef = useRef({ stt: 'browser', tts: 'browser' })
    const [engine, setEngine] = useState({ stt: 'browser', tts: 'browser' })

    // Which engines the backend has. Local wins whenever it is installed.
    useEffect(() => {
        aiApi
            .voiceStatus()
            .then(({ data }) => {
                const next = {
                    stt: data.stt?.local_whisper?.installed && data.stt?.preferred !== 'browser' ? 'local' : 'browser',
                    tts: data.tts?.piper_available && data.tts?.preferred !== 'browser' ? 'piper' : 'browser',
                }
                engineRef.current = next
                setEngine(next)
            })
            .catch(() => {})
    }, [])

    // Keep a ref alongside the state: the recognition callbacks are created
    // once and would otherwise close over a stale `ambient`.
    useEffect(() => {
        ambientRef.current = ambient
    }, [ambient])

    useEffect(() => {
        listeningRef.current = listening
    }, [listening])

    // --- Microphone level, for the core's reaction ------------------------
    // Purely visual. The recogniser gives no amplitude, so the orb would sit
    // dead still while you talk without this.
    const startMeter = useCallback(async () => {
        if (audioCleanup.current) return
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
            const context = new (window.AudioContext || window.webkitAudioContext)()
            if (context.state === 'suspended') await context.resume().catch(() => {})
            const source = context.createMediaStreamSource(stream)
            const analyser = context.createAnalyser()
            analyser.fftSize = 256
            source.connect(analyser)

            const data = new Uint8Array(analyser.frequencyBinCount)
            let raf
            const tick = () => {
                analyser.getByteFrequencyData(data)
                const avg = data.reduce((a, b) => a + b, 0) / data.length
                setAmplitude(Math.min(1, avg / 90))
                raf = requestAnimationFrame(tick)
            }
            tick()

            audioCleanup.current = () => {
                cancelAnimationFrame(raf)
                stream.getTracks().forEach((t) => t.stop())
                context.close()
                audioCleanup.current = null
                setAmplitude(0)
            }
        } catch {
            // No mic permission just means no reactive orb — recognition may
            // still work, so this is never fatal.
        }
    }, [])

    const stopMeter = useCallback(() => {
        audioCleanup.current?.()
    }, [])

    // --- Speaking ---------------------------------------------------------

    /**
     * Speak.
     *
     * Chrome's speechSynthesis has four well-known ways of silently doing
     * nothing, and this hits all four in normal use:
     *
     *  1. The utterance is garbage-collected mid-speech if nothing holds a
     *     reference — the single most common cause of "no sound at all".
     *  2. cancel() immediately followed by speak() in the same tick drops the
     *     new utterance. It needs a tick to settle.
     *  3. getVoices() is empty until the async voice list loads, and assigning
     *     a voice from an empty list can wedge the queue.
     *  4. It auto-pauses after ~15 seconds of continuous speech unless
     *     resume() is called periodically.
     *
     * Recognition is also paused while speaking, otherwise the mic hears the
     * reply and the assistant starts answering itself.
     */
    const speakBrowser = useCallback(
        (text) => {
            if (!text) return
            if (!speechSupported) {
                setError('This browser cannot speak. Try Chrome, Edge or Safari.')
                return
            }

            const synth = window.speechSynthesis
            const wasListening = listeningRef.current

            const run = () => {
                const utterance = new SpeechSynthesisUtterance(text)
                utterance.rate = 1.03
                utterance.pitch = 0.9
                utterance.volume = 1
                utterance.lang = 'en-IN'

                // Prefer en-IN, then en-GB — the default en-US voice mangles
                // Indian company names badly.
                const voices = synth.getVoices()
                const preferred =
                    voices.find((v) => /en[-_]IN/i.test(v.lang)) ||
                    voices.find((v) => /en[-_]GB/i.test(v.lang)) ||
                    voices.find((v) => v.lang?.startsWith('en'))
                if (preferred) utterance.voice = preferred

                utterance.onstart = () => {
                    setSpeaking(true)
                    setError(null)
                    // (4) keep-alive against Chrome's ~15s auto-pause
                    clearInterval(keepAlive.current)
                    keepAlive.current = setInterval(() => {
                        if (synth.speaking && !synth.paused) synth.resume()
                    }, 5000)
                }

                const finish = () => {
                    clearInterval(keepAlive.current)
                    setSpeaking(false)
                    utteranceRef.current = null
                    // Resume ambient listening once we've stopped talking.
                    if (wasListening && ambientRef.current && !shouldRestart.current) {
                        shouldRestart.current = true
                        try {
                            recognitionRef.current?.start()
                        } catch {
                            /* already running */
                        }
                    }
                }

                utterance.onend = finish
                utterance.onerror = (event) => {
                    // 'interrupted' and 'canceled' are normal when the user
                    // barges in; anything else is worth surfacing.
                    if (!['interrupted', 'canceled'].includes(event.error)) {
                        setError(`Speech failed: ${event.error}`)
                    }
                    finish()
                }

                // (1) hold a reference so the browser can't collect it
                utteranceRef.current = utterance
                synth.speak(utterance)

                // Some builds need a nudge when speak() is called right after
                // a cancel — if nothing started, try once more.
                setTimeout(() => {
                    if (!synth.speaking && !synth.pending && utteranceRef.current === utterance) {
                        try {
                            synth.resume()
                            synth.speak(utterance)
                        } catch {
                            /* give up quietly — the text is on screen anyway */
                        }
                    }
                }, 250)
            }

            // Stop the recogniser so it doesn't transcribe our own voice.
            if (wasListening && !localRef.current) {
                shouldRestart.current = false
                try {
                    recognitionRef.current?.stop()
                } catch {
                    /* not running */
                }
            }

            synth.cancel()

            // (3) wait for voices, then (2) give cancel() a tick to settle
            if (synth.getVoices().length === 0) {
                const onVoices = () => {
                    window.speechSynthesis.removeEventListener?.('voiceschanged', onVoices)
                    setTimeout(run, 60)
                }
                window.speechSynthesis.addEventListener?.('voiceschanged', onVoices)
                setTimeout(run, 500) // fallback if the event never fires
            } else {
                setTimeout(run, 60)
            }
        },
        []
    )

    // The local Piper voice. The mic is paused while it plays, or the
    // assistant would hear itself and answer its own reply.
    const speak = useCallback(
        async (text) => {
            if (!text) return
            if (engineRef.current.tts !== 'piper') return speakBrowser(text)
            try {
                audioRef.current?.pause()
                localRef.current?.pause(true)
                const { data } = await aiApi.speak(text)
                const url = URL.createObjectURL(data)
                const audio = new Audio(url)
                audioRef.current = audio
                const finish = () => {
                    URL.revokeObjectURL(url)
                    if (audioRef.current === audio) audioRef.current = null
                    setSpeaking(false)
                    // A short tail so the room's echo isn't picked up.
                    setTimeout(() => localRef.current?.pause(false), 250)
                }
                audio.onended = finish
                audio.onerror = finish
                setSpeaking(true)
                setError(null)
                await audio.play()
            } catch {
                localRef.current?.pause(false)
                setSpeaking(false)
                speakBrowser(text) // local voice failed; the browser one is better than silence
            }
        },
        [speakBrowser]
    )

    const stopSpeaking = useCallback(() => {
        if (audioRef.current) {
            audioRef.current.pause()
            audioRef.current = null
            localRef.current?.pause(false)
        }
        clearInterval(keepAlive.current)
        if (speechSupported) window.speechSynthesis.cancel()
        utteranceRef.current = null
        setSpeaking(false)
    }, [])

    // --- Asking -----------------------------------------------------------

    const ask = useCallback(
        async (text) => {
            const question = (text || '').trim()
            if (!question) return

            setThinking(true)
            setError(null)
            try {
                // Command routing first. "Start the feed" and "show breaks" are
                // actions, not questions, and routing them through the
                // conversational model would cost 20 seconds and probably
                // produce prose instead of doing the thing.
                //
                // The backend falls through to the brain itself when nothing
                // matches, so this single call covers both paths and there is
                // no client-side guess about which one applies.
                const { data } = await aiApi.voiceCommand(question, true)

                const answer = {
                    ...data,
                    // Normalised so consumers do not care which path answered.
                    response: data.response || data.speech,
                    speech: data.speech,
                }
                onAnswer?.(answer)
                if (autoSpeak && data.speech) speak(data.speech)

                // A command may ask the UI to move — "show me the breaks"
                // should land on the breaks panel, not just describe it.
                if (data.navigate) {
                    onNavigate?.(data.navigate)
                }
                return answer
            } catch (err) {
                const message =
                    err?.response?.data?.detail || err.message || 'Could not reach Tradeo'
                setError(message)
                if (autoSpeak) speak("I couldn't reach my reasoning engine.")
            } finally {
                setThinking(false)
            }
        },
        [sessionId, onAnswer, onNavigate, autoSpeak, speak]
    )

    // --- Recognition ------------------------------------------------------

    const commit = useCallback(() => {
        const text = finalText.current.trim()
        finalText.current = ''
        setInterim('')
        if (!text) return

        // In ambient mode only act on speech addressed to the assistant,
        // otherwise every conversation in the room becomes a query.
        if (ambientRef.current) {
            const match = wakeRegex(wakeWord).exec(text)
            if (!match) return
            const addressed = text.slice(match.index + match[0].length).replace(/^[\s,.:!?-]+/, '')
            if (addressed.length < 2) return
            setTranscript(addressed)
            onTranscript?.(addressed)
            ask(addressed)
            return
        }

        setTranscript(text)
        onTranscript?.(text)
        ask(text)
    }, [wakeWord, ask, onTranscript])

    const buildRecognition = useCallback(() => {
        if (!SpeechRecognition) return null

        const recognition = new SpeechRecognition()
        recognition.continuous = true
        recognition.interimResults = true
        recognition.lang = 'en-IN'
        recognition.maxAlternatives = 1

        recognition.onresult = (event) => {
            let interimText = ''
            for (let i = event.resultIndex; i < event.results.length; i += 1) {
                const result = event.results[i]
                if (result.isFinal) finalText.current += `${result[0].transcript} `
                else interimText += result[0].transcript
            }
            setInterim(interimText)

            clearTimeout(silenceTimer.current)
            silenceTimer.current = setTimeout(commit, SILENCE_MS)
        }

        recognition.onerror = (event) => {
            if (event.error === 'no-speech' || event.error === 'aborted') return
            if (event.error === 'network') {
                // Google's recogniser is unreachable; switch to local Whisper.
                shouldRestart.current = false
                engineRef.current = { ...engineRef.current, stt: 'local' }
                setEngine(engineRef.current)
                setError('Browser recognition needs Google. Switched to local recognition; press the mic again.')
                return
            }
            setError(
                event.error === 'not-allowed'
                    ? 'Microphone permission denied'
                    : `Recognition error: ${event.error}`
            )
            if (event.error === 'not-allowed') {
                shouldRestart.current = false
                setListening(false)
                setAmbient(false)
            }
        }

        recognition.onend = () => {
            // Chrome ends the session every ~60s regardless of `continuous`,
            // so ambient listening has to restart itself or it silently dies.
            if (shouldRestart.current) {
                try {
                    recognition.start()
                    return
                } catch {
                    /* already starting — ignore */
                }
            }
            setListening(false)
            stopMeter()
        }

        return recognition
    }, [commit, stopMeter])

    // Local recognition: record here, transcribe with Whisper on the backend.
    const startLocal = useCallback(
        async (ambientMode) => {
            setError(null)
            finalText.current = ''
            setInterim('')
            ambientRef.current = ambientMode
            setAmbient(ambientMode)
            try {
                const listener = await startLocalListener({
                    pushToTalk: !ambientMode,
                    onError: (message) => {
                        localRef.current?.stop()
                        localRef.current = null
                        setListening(false)
                        setInterim('')
                        setError(message)
                    },
                    onLevel: setAmplitude,
                    onSpeechStart: () => setInterim('hearing you…'),
                    onUtterance: async (wav) => {
                        // Push-to-talk takes one utterance, then the mic closes.
                        if (!ambientRef.current) {
                            localRef.current?.stop()
                            localRef.current = null
                            setListening(false)
                        }
                        if (!wav) {
                            setInterim('')
                            setError('Nothing was recorded — click the mic, speak, then click again to send.')
                            return
                        }
                        setInterim('transcribing…')
                        try {
                            const { data } = await aiApi.transcribe(wav)
                            setInterim('')
                            const heard = (data.text || '').trim()
                            if (!heard) {
                                setError("I couldn't make out any words — check the microphone and try again.")
                                return
                            }
                            // The backend decides the wake word, with the
                            // spellings Whisper actually produces ("Tradio").
                            if (ambientRef.current && !data.addressed) {
                                // Say so rather than silently ignoring it.
                                setInterim(`Heard “${heard}” — start with “${wakeWord}” while always-listening`)
                                setTimeout(() => setInterim(''), 4000)
                                return
                            }
                            const command = (data.command || heard).trim()
                            if (command.length < 2) return
                            setTranscript(command)
                            onTranscript?.(command)
                            ask(command)
                        } catch (err) {
                            setInterim('')
                            setError(err?.response?.data?.detail || 'Local transcription failed')
                        }
                    },
                })
                localRef.current = listener
                setListening(true)
                if (!ambientMode) setInterim('listening — click the mic again to send')
            } catch (err) {
                setError(
                    err?.name === 'NotAllowedError'
                        ? 'Microphone permission denied'
                        : `Could not open the microphone: ${err?.message || err}`
                )
                setListening(false)
                setAmbient(false)
            }
        },
        [wakeWord, ask, onTranscript]
    )

    const start = useCallback(
        (ambientMode = false) => {
            if (listening) return
            if (engineRef.current.stt === 'local' || !SpeechRecognition) {
                startLocal(ambientMode)
                return
            }
            if (!SpeechRecognition) {
                setError('This browser has no speech recognition. Try Chrome or Edge.')
                return
            }
            if (listening) return

            setError(null)
            finalText.current = ''
            setInterim('')
            ambientRef.current = ambientMode
            setAmbient(ambientMode)
            shouldRestart.current = ambientMode

            const recognition = buildRecognition()
            recognitionRef.current = recognition
            try {
                recognition.start()
                setListening(true)
                startMeter()
            } catch {
                setError('Could not start the microphone')
            }
        },
        [listening, buildRecognition, startMeter, startLocal]
    )

    const stop = useCallback(() => {
        localRef.current?.stop()
        localRef.current = null
        shouldRestart.current = false
        ambientRef.current = false
        setAmbient(false)
        clearTimeout(silenceTimer.current)
        try {
            recognitionRef.current?.stop()
        } catch {
            /* not running */
        }
        setListening(false)
        setInterim('')
        stopMeter()
    }, [stopMeter])

    const toggle = useCallback(() => {
        // Second click on push-to-talk sends what was said, not discards it.
        if (listening && localRef.current && !ambientRef.current) {
            setInterim('transcribing…')
            localRef.current.finish()
            return
        }
        if (listening) stop()
        else start(false)
    }, [listening, start, stop])

    const toggleAmbient = useCallback(() => {
        if (ambient) stop()
        else start(true)
    }, [ambient, start, stop])

    // Voices load asynchronously in Chrome; touching the list early makes the
    // first utterance use the right one instead of the US default.
    useEffect(() => {
        if (!speechSupported) return
        window.speechSynthesis.getVoices()
        const handler = () => window.speechSynthesis.getVoices()
        window.speechSynthesis.addEventListener?.('voiceschanged', handler)
        return () => window.speechSynthesis.removeEventListener?.('voiceschanged', handler)
    }, [])

    useEffect(
        () => () => {
            shouldRestart.current = false
            clearTimeout(silenceTimer.current)
            clearInterval(keepAlive.current)
            try {
                recognitionRef.current?.abort()
            } catch {
                /* nothing running */
            }
            localRef.current?.stop()
            audioRef.current?.pause()
            stopMeter()
            if (speechSupported) window.speechSynthesis.cancel()
        },
        [stopMeter]
    )

    return {
        supported: voiceSupported,
        engine,
        speechSupported,
        listening,
        ambient,
        speaking,
        thinking,
        transcript,
        interim,
        amplitude,
        error,
        start,
        stop,
        toggle,
        toggleAmbient,
        ask,
        speak,
        stopSpeaking,
    }
}

export default useVoice
