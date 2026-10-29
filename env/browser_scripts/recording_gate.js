(() => {
    const id = "__recordingGate";
    const stateKey = "__recordingGateState";

    // The gate belongs to the top-level recording surface. Installing it inside
    // game iframes would intercept their input without being removable through
    // the page-level recording controls.
    if (window.top !== window) return;

    try {
        if (window.sessionStorage.getItem(stateKey) === "released") return;
    } catch (_) {
        // Local benchmark pages have sessionStorage. If a page disables it,
        // fail closed during initial preparation and let finish_recording_gate
        // remove the current overlay.
    }

    function mount() {
        if (!document.documentElement || document.getElementById(id)) return;
        const gate = document.createElement("div");
        gate.id = id;
        gate.tabIndex = 0;
        gate.textContent = "Preparing...";
        Object.assign(gate.style, {
            position: "fixed",
            inset: "0",
            zIndex: "2147483646",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#000",
            color: "#fff",
            font: "600 28px/1.35 system-ui, sans-serif",
            textAlign: "center",
            pointerEvents: "all"
        });
        gate.addEventListener("keydown", event => {
            event.preventDefault();
            event.stopPropagation();
        });
        document.documentElement.appendChild(gate);
        gate.focus({preventScroll: true});
    }

    if (document.documentElement) mount();
    else document.addEventListener("DOMContentLoaded", mount, {once: true});
})();
