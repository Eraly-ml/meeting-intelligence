// One bounded interaction with the visible meeting page. No page text is logged.
(() => {
  const host = location.hostname;
  const allowed = host === 'meet.google.com' || host === 'zoom.us' || host.endsWith('.zoom.us') || ['teams.microsoft.com', 'teams.live.com', 'teams.cloud.microsoft'].includes(host);
  if (!allowed) return { state: 'blocked', message: 'The provider requires sign-in or redirected outside its meeting page. Sign in once using the station browser.' };
  const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const elements = [...document.querySelectorAll('button, [role="button"], a')].filter(visible);
  const label = el => (el.getAttribute('aria-label') || el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const find = regex => elements.find(el => regex.test(label(el)) && !el.disabled && el.getAttribute('aria-disabled') !== 'true');
  const text = document.body?.innerText || '';
  if (/403\. That’s an error|403\. That's an error|you can.?t join this (video )?call|not allowed to join|request to join was denied|couldn't join|meeting (does not exist|not found)|invalid meeting|не удалось присоединиться|отказано в доступе/i.test(text)) {
    return { state: 'blocked', message: 'The meeting provider refused this join request. Check the meeting link, guest access or the station account.' };
  }
  if (/you('ve| have) (left|been removed)|you left the (meeting|call)|meeting has ended|call has ended|вы покинули|встреча завершена/i.test(text)) return { state: 'ended', message: 'The meeting ended. Saving the recording.' };
  if (/asking to (be let in|join)|someone.*let you in|waiting for (the )?(host|organizer)|waiting in the lobby|запрос отправлен|ожидание допуска/i.test(text)) return { state: 'waiting', message: 'Join request sent. Waiting for the host to admit Meeting Station.' };
  const leave = find(/^(leave call|leave meeting|leave|выйти из встречи|покинуть вызов)(\s*\(.*\))?$/i);
  if (leave) {
    for (const media of document.querySelectorAll('audio')) {
      if (media.srcObject && media.paused && !media.muted) void media.play().catch(() => {});
    }
    const audio = find(/^(join audio|join with computer audio|join computer audio)$/i);
    if (audio) audio.click();
    const mute = find(/^(turn off microphone|mute microphone|mute my audio|mute)(\s*\(.*\))?$/i);
    const camera = find(/^(turn off camera|stop video)(\s*\(.*\))?$/i);
    if (mute) mute.click();
    if (camera) camera.click();
    return { state: 'joined', message: 'Meeting Station joined. Recording incoming audio with its microphone and camera off.' };
  }
  if (document.querySelector('input[type="password"]') || /sign in to join|you need to sign in|требуется вход/i.test(text)) return { state: 'blocked', message: 'This meeting requires an account or passcode. Complete that setup once in the station browser.' };
  if (document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"]')) return { state: 'blocked', message: 'The provider requires a human verification check in the station browser.' };
  const withoutMedia = find(/^(continue without (microphone and camera|microphone|camera)|join without audio|dismiss|got it|продолжить без.*|понятно)$/i);
  if (withoutMedia) { withoutMedia.click(); return { state: 'joining', message: 'Continuing with microphone and camera off.' }; }
  const browser = find(/^(join from your browser|continue on this browser|join on the web instead|continue in this browser|join from browser)$/i);
  if (browser) { browser.click(); return { state: 'joining', message: 'Opening the provider’s browser meeting.' }; }
  const inputs = [...document.querySelectorAll('input')].filter(visible);
  const name = inputs.find(el => /your name|enter.*name|display.?name|(?:^|\s)(name|inputname)(?:\s|$)|имя/i.test([el.placeholder, el.getAttribute('aria-label'), el.name, el.id].join(' ')));
  if (name && name.value !== 'Meeting Station (recording)') {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(name, 'Meeting Station (recording)');
    name.dispatchEvent(new Event('input', { bubbles: true }));
    name.dispatchEvent(new Event('change', { bubbles: true }));
  }
  const mute = find(/^(turn off microphone|mute microphone|mute my audio|mute)(\s*\(.*\))?$/i);
  const camera = find(/^(turn off camera|stop video)(\s*\(.*\))?$/i);
  if (mute) mute.click();
  if (camera) camera.click();
  const join = find(/^(ask to join|join now|join meeting|join|присоединиться|отправить запрос|присоединиться сейчас)$/i);
  if (join && (!window.__stationJoinClick || Date.now() - window.__stationJoinClick > 10000)) {
    window.__stationJoinClick = Date.now();
    join.click();
    return { state: 'waiting', message: 'Join request sent. Waiting for admission.' };
  }
  return { state: 'joining', message: 'Preparing the meeting page.' };
})()
