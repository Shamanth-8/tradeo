/**
 * Local speech capture for the Whisper path.
 *
 * Chrome's SpeechRecognition streams audio to Google and fails with
 * "network" wherever that service is unreachable (Chromium, Electron, a
 * blocked network). This records the microphone here instead, finds where
 * speech starts and stops, and hands each utterance over as a WAV for the
 * backend's Whisper to transcribe. Nothing leaves the machine.
 *
 * Raw PCM rather than MediaRecorder, for two reasons: half a second from
 * before speech was detected is kept, so the first word isn't clipped, and a
 * WAV cut from the middle of a stream is always valid, which a WebM chunk
 * is not.
 */

const TARGET_RATE = 16000 // what Whisper wants
const PRE_ROLL_S = 0.5
const MAX_UTTERANCE_S = 15
// Quiet laptop mics with auto-gain rarely cross a fixed 0.015 RMS; the old
// floor meant push-to-talk never saw speech and never finished.
const MIN_THRESHOLD = 0.006

export function downsample(buffer, fromRate) {
    if (fromRate === TARGET_RATE) return buffer
    const ratio = fromRate / TARGET_RATE
    const out = new Float32Array(Math.floor(buffer.length / ratio))
    for (let i = 0; i < out.length; i += 1) {
        // Average the window: a cheap low-pass that avoids aliasing hiss.
        const start = Math.floor(i * ratio)
        const end = Math.min(buffer.length, Math.floor((i + 1) * ratio))
        let sum = 0
        for (let j = start; j < end; j += 1) sum += buffer[j]
        out[i] = sum / Math.max(1, end - start)
    }
    return out
}

export function encodeWav(samples) {
    const view = new DataView(new ArrayBuffer(44 + samples.length * 2))
    const text = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)))
    text(0, 'RIFF')
    view.setUint32(4, 36 + samples.length * 2, true)
    text(8, 'WAVE')
    text(12, 'fmt ')
    view.setUint32(16, 16, true)
    view.setUint16(20, 1, true) // PCM
    view.setUint16(22, 1, true) // mono
    view.setUint32(24, TARGET_RATE, true)
    view.setUint32(28, TARGET_RATE * 2, true)
    view.setUint16(32, 2, true)
    view.setUint16(34, 16, true)
    text(36, 'data')
    view.setUint32(40, samples.length * 2, true)
    for (let i = 0; i < samples.length; i += 1) {
        const s = Math.max(-1, Math.min(1, samples[i]))
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    }
    return new Blob([view], { type: 'audio/wav' })
}

function concat(chunks) {
    const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0))
    let offset = 0
    for (const c of chunks) {
        out.set(c, offset)
        offset += c.length
    }
    return out
}

/**
 * Start listening. Returns a controller with stop() and pause(bool).
 *
 *   onLevel(0..1)       mic level, for the orb
 *   onSpeechStart()     speech detected
 *   onUtterance(blob)   one finished utterance as WAV
 *   silenceMs           pause that ends an utterance
 *   pushToTalk          record from the first moment, not from detected
 *                       speech: the clip ends on finish(), on a pause after
 *                       speech, or at MAX_UTTERANCE_S — never by waiting
 *                       for a loudness threshold that may never be crossed.
 */
export async function startLocalListener({ onLevel, onSpeechStart, onUtterance, onError, silenceMs = 1100, pushToTalk = false }) {
    const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    const context = new (window.AudioContext || window.webkitAudioContext)()
    // Created after an await, Chrome starts the context "suspended": the mic
    // light is on but not one sample is processed — the "always on, never
    // listening" bug. Resume it explicitly.
    if (context.state === 'suspended') {
        try {
            await context.resume()
        } catch {
            /* reported by the watchdog below */
        }
    }
    const source = context.createMediaStreamSource(stream)
    // ScriptProcessor is deprecated but works in every browser and Electron
    // without shipping a separate worklet file.
    const processor = context.createScriptProcessor(4096, 1, 1)
    const rate = context.sampleRate

    let paused = false
    let speaking = pushToTalk
    let chunks = []
    let preRoll = []
    let lastVoice = 0
    let started = performance.now()
    let noiseFloor = 0.005
    let heardVoice = false
    let finished = false

    const finish = () => {
        if (finished) return
        finished = true
        speaking = false
        const audio = concat(chunks)
        chunks = []
        // Under ~0.3 s is a click, not a question.
        if (audio.length / rate > 0.3) onUtterance?.(encodeWav(downsample(audio, rate)))
        else onUtterance?.(null)
    }

    let frames = 0
    processor.onaudioprocess = (event) => {
        frames += 1
        const input = new Float32Array(event.inputBuffer.getChannelData(0))
        let sum = 0
        for (let i = 0; i < input.length; i += 1) sum += input[i] * input[i]
        const rms = Math.sqrt(sum / input.length)
        onLevel?.(Math.min(1, rms * 12))

        if (paused) {
            preRoll = []
            return
        }

        const now = performance.now()

        if (pushToTalk) {
            if (finished) return
            chunks.push(input)
            // The first half-second sets the room's noise level.
            if (now - started < 500) noiseFloor = Math.max(noiseFloor, rms)
            else if (rms > Math.max(MIN_THRESHOLD, noiseFloor * 2)) {
                if (!heardVoice) onSpeechStart?.()
                heardVoice = true
                lastVoice = now
            }
            if ((heardVoice && now - lastVoice > silenceMs) || now - started > MAX_UTTERANCE_S * 1000) {
                finish()
            }
            return
        }

        // Track the room's noise so a fan or AC isn't mistaken for speech.
        if (!speaking) noiseFloor = noiseFloor * 0.95 + rms * 0.05
        const threshold = Math.max(MIN_THRESHOLD * 2, noiseFloor * 3)

        if (rms > threshold) {
            lastVoice = now
            if (!speaking) {
                speaking = true
                started = now
                chunks = [...preRoll]
                onSpeechStart?.()
            }
        }

        if (speaking) {
            chunks.push(input)
            const tooLong = now - started > MAX_UTTERANCE_S * 1000
            if (now - lastVoice > silenceMs || tooLong) {
                speaking = false
                const audio = concat(chunks)
                chunks = []
                // Ignore clicks and coughs: under ~0.4s of audio isn't a question.
                if (audio.length / rate > 0.4 + silenceMs / 1000) {
                    onUtterance?.(encodeWav(downsample(audio, rate)))
                }
            }
        } else {
            preRoll.push(input)
            const keep = Math.ceil((PRE_ROLL_S * rate) / input.length)
            if (preRoll.length > keep) preRoll.shift()
        }
    }

    source.connect(processor)
    processor.connect(context.destination) // required for onaudioprocess to fire

    // Never leave the mic "on" silently: if no audio arrives, say so.
    const watchdog = setTimeout(() => {
        if (frames === 0) onError?.(`The microphone gave no audio (audio context ${context.state}). Click the mic again, or check the browser's microphone permission.`)
    }, 3000)

    return {
        // Push-to-talk: send what was recorded now (the second click).
        finish,
        pause(value) {
            paused = value
            if (value) {
                speaking = false
                chunks = []
            }
        },
        stop() {
            clearTimeout(watchdog)
            processor.onaudioprocess = null
            try {
                processor.disconnect()
                source.disconnect()
            } catch {
                /* already disconnected */
            }
            stream.getTracks().forEach((t) => t.stop())
            context.close()
            onLevel?.(0)
        },
    }
}
