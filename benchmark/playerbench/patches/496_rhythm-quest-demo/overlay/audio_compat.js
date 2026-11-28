/* Chromium builds without AAC: preserve original sound data and decode local Ogg equivalents. */
(() => {
  window.rhythmQuestAudioContexts = [];
  window.rhythmQuestAudioPending = 0;
  window.rhythmQuestAudioErrors = [];
  for (const key of ['AudioContext', 'webkitAudioContext']) {
    const Original = window[key];
    if (!Original) continue;
    window[key] = new Proxy(Original, {construct(target,args) {
      const context = Reflect.construct(target,args);
      window.rhythmQuestAudioContexts.push(context);
      return context;
    }});
  }
  if (document.createElement('audio').canPlayType('audio/mp4; codecs="mp4a.40.2"')) return;
  const manifest = fetch('audio_manifest.json').then(r => r.json());
  const proto = (window.AudioContext || window.webkitAudioContext).prototype;
  const nativeDecode = proto.decodeAudioData;
  proto.decodeAudioData = function (data, onSuccess, onError) {
    const view = new Uint8Array(data);
    if (view[4] !== 102 || view[5] !== 116 || view[6] !== 121 || view[7] !== 112) {
      return nativeDecode.call(this, data, onSuccess, onError);
    }
    window.rhythmQuestAudioPending++;
    const promise = Promise.all([manifest, crypto.subtle.digest('SHA-256', data)])
      .then(([mapping, hash]) => {
        const key = Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
        if (!mapping[key]) throw new Error('Missing offline audio equivalent: ' + key);
        return fetch(mapping[key]).then(r => { if (!r.ok) throw new Error('Audio fetch failed'); return r.arrayBuffer(); });
      }).then(ogg => nativeDecode.call(this, ogg)).catch(error => {
        window.rhythmQuestAudioErrors.push(String(error));
        throw error;
      }).finally(() => { window.rhythmQuestAudioPending--; });
    if (onSuccess || onError) promise.then(onSuccess, onError);
    return promise;
  };
})();
