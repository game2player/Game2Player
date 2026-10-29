(() => {
    const isLocal = raw => {
        try {
            const url = new URL(String(raw), window.location.href);
            return ["127.0.0.1", "localhost", "::1", "[::1]", ""].includes(url.hostname);
        } catch (_) {
            return false;
        }
    };

    // BrowserContext routing blocks page HTTP(S) traffic. Extension recording
    // must leave its own service worker enabled, so prevent the game page from
    // registering a worker whose fetches could bypass that route.
    try {
        const container = navigator.serviceWorker;
        if (container && typeof container.register === "function") {
            Object.defineProperty(container, "register", {
                configurable: true,
                value: async () => {
                    throw new DOMException(
                        "Page service workers are disabled in offline recording mode",
                        "NotAllowedError",
                    );
                },
            });
        }
    } catch (_) {
        // The normal browser-context route remains the primary network gate.
    }

    const NativeWebSocket = window.WebSocket;
    if (typeof NativeWebSocket === "function") {
        function OfflineWebSocket(url, protocols) {
            if (!isLocal(url)) {
                throw new DOMException(
                    "External WebSockets are disabled in offline recording mode",
                    "SecurityError",
                );
            }
            return protocols === undefined
                ? new NativeWebSocket(url)
                : new NativeWebSocket(url, protocols);
        }
        OfflineWebSocket.prototype = NativeWebSocket.prototype;
        Object.setPrototypeOf(OfflineWebSocket, NativeWebSocket);
        Object.defineProperties(OfflineWebSocket, {
            CONNECTING: {value: NativeWebSocket.CONNECTING},
            OPEN: {value: NativeWebSocket.OPEN},
            CLOSING: {value: NativeWebSocket.CLOSING},
            CLOSED: {value: NativeWebSocket.CLOSED},
        });
        window.WebSocket = OfflineWebSocket;
    }

    const NativeEventSource = window.EventSource;
    if (typeof NativeEventSource === "function") {
        function OfflineEventSource(url, options) {
            if (!isLocal(url)) {
                throw new DOMException(
                    "External event streams are disabled in offline recording mode",
                    "SecurityError",
                );
            }
            return new NativeEventSource(url, options);
        }
        OfflineEventSource.prototype = NativeEventSource.prototype;
        Object.setPrototypeOf(OfflineEventSource, NativeEventSource);
        window.EventSource = OfflineEventSource;
    }
})();
