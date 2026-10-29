(() => {
    // The runtime plays every game in a fixed-size window. A game that asks for
    // fullscreen on input (Unity templates often do) would put headless Chromium in
    // a state where screenshots taken from the compositor surface, which are what a
    // player sees and what the video records, show the game in one corner. The
    // request is accepted and changes nothing.
    for (const name of ["requestFullscreen", "webkitRequestFullscreen"]) {
        if (typeof Element.prototype[name] === "function") {
            Element.prototype[name] = function () {
                return Promise.resolve();
            };
        }
    }
})();
