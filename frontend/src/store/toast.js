import { create } from 'zustand'

/**
 * Transient notifications.
 *
 * Separate from the main app store because the lifecycle is different: these
 * own timers, and a component unmounting must not leave one running against a
 * store slice nothing is reading. Keeping them apart also means a page can
 * subscribe to toasts without re-rendering on every portfolio update.
 *
 * Errors do not auto-dismiss. A message that says a run failed and then
 * disappears before it is read is worse than no message, because the user now
 * knows something went wrong and cannot find out what.
 */

const DURATIONS = { ok: 3200, info: 4000, warn: 6500, error: null }

let nextId = 1

export const useToastStore = create((set, get) => ({
    toasts: [],

    push: (tone, message, detail = '') => {
        const id = nextId++
        set((state) => ({
            toasts: [...state.toasts.slice(-4), { id, tone, message, detail, at: Date.now() }],
        }))

        const duration = DURATIONS[tone]
        if (duration) {
            setTimeout(() => get().dismiss(id), duration)
        }
        return id
    },

    dismiss: (id) =>
        set((state) => ({ toasts: state.toasts.filter((toast) => toast.id !== id) })),

    clear: () => set({ toasts: [] }),
}))

/**
 * The calling surface.
 *
 * Two properties matter, and both are easy to lose. It subscribes only to
 * `push`, so a component that merely *sends* toasts never re-renders when one
 * appears somewhere else. And the returned object is referentially stable —
 * built once outside React rather than on each render — so it is safe to put
 * in a `useCallback` dependency array without silently defeating the memo.
 */
const TOAST = {
    ok: (message, detail) => useToastStore.getState().push('ok', message, detail),
    info: (message, detail) => useToastStore.getState().push('info', message, detail),
    warn: (message, detail) => useToastStore.getState().push('warn', message, detail),
    error: (message, detail) => useToastStore.getState().push('error', message, detail),
    dismiss: (id) => useToastStore.getState().dismiss(id),
}

export function useToast() {
    return TOAST
}
