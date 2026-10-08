'use strict';
const el = id => document.getElementById(id);
let csrf = '', creator = null, previewURL = null, sending = false;
let requestKey = null, allowPublic = false, pollTimer = null;
const tell = text => { el('notice').textContent = text; };
async function api(path, options = {}) {
  const response = await fetch(path, {...options, credentials:'same-origin', headers:{'X-CSRF-Token':csrf, ...(options.headers || {})}});
  const data = await response.json();
  if (!response.ok) throw new Error(`${data.detail || 'İşlem başarısız'}${data.job ? ` — İş kimliği: ${data.job.id}. Durumu kontrol edin; yeni anahtarla tekrar göndermeyin.` : ''}`);
  return data;
}
function syncForm() {
  const direct = el('mode').value === 'direct';
  el('direct-options').hidden = !direct;
  el('privacy').required = direct;
  el('inbox-note').hidden = direct;
  const publicChoice = direct && el('privacy').value && el('privacy').value !== 'SELF_ONLY';
  el('public-label').hidden = !publicChoice;
  el('public-confirm').required = Boolean(publicChoice);
  const commercial = el('commercial').checked;
  el('commercial-options').hidden = !commercial;
  if (!commercial) { el('branded').checked = false; el('organic').checked = false; }
  el('branded').disabled = el('privacy').value === 'SELF_ONLY';
  if (el('branded').disabled) el('branded').checked = false;
  for (const option of el('privacy').options) {
    if (option.value === 'SELF_ONLY') option.disabled = el('branded').checked;
  }
  el('commercial-notice').hidden = !commercial;
  el('commercial-label').textContent = el('branded').checked
    ? "Videonuz 'Ücretli ortaklık' olarak etiketlenecek. Göndererek TikTok Markalı İçerik Politikası ve Müzik Kullanım Onayı'nı kabul edersiniz."
    : "Kendi markanız seçildiğinde videonuz 'Tanıtım içeriği' olarak etiketlenecek. Göndererek TikTok Müzik Kullanım Onayı'nı kabul edersiniz.";
  const missingDisclosure = direct && commercial && !el('branded').checked && !el('organic').checked;
  el('submit').disabled = sending || missingDisclosure;
  el('submit').title = missingDisclosure ? 'Ticari içerik için en az bir açıklama seçiniz.' : '';
}
async function loadCreator() {
  creator = await api('/api/creator');
  el('creator').textContent = `TikTok hesabı: ${creator.creator_nickname} (@${creator.creator_username}) — en fazla ${creator.max_video_post_duration_sec} saniye`;
  el('privacy').replaceChildren(new Option('Seçiniz', ''));
  const names = {SELF_ONLY:'Sadece ben', PUBLIC_TO_EVERYONE:'Herkese açık', MUTUAL_FOLLOW_FRIENDS:'Arkadaşlar', FOLLOWER_OF_CREATOR:'Takipçiler'};
  for (const level of creator.privacy_level_options) {
    const option = new Option(names[level] || level, level);
    if (!allowPublic && level !== 'SELF_ONLY') { option.disabled = true; option.text += ' (sunucu izni gerekli)'; }
    el('privacy').add(option);
  }
  for (const name of ['comment','duet','stitch']) {
    el(name).disabled = creator[`${name}_disabled`];
    el(name).checked = false;
  }
  syncForm();
}
async function loadJobs(check = false) {
  const jobs = await api('/api/jobs');
  el('jobs').replaceChildren();
  for (let job of jobs) {
    if (check) {
      try { job = await api(`/api/jobs/${encodeURIComponent(job.id)}`); }
      catch (error) { tell(error.message); }
    }
    const item = document.createElement('li');
    item.textContent = `${job.id}: ${job.phase}${job.result?.fail_reason ? ` (${job.result.fail_reason})` : ''}${job.result?.post_ids ? ` — Gönderi: ${job.result.post_ids.join(', ')}` : ''}`;
    el('jobs').append(item);
  }
}
el('video').addEventListener('change', () => {
  if (previewURL) URL.revokeObjectURL(previewURL);
  if (el('video').files[0]) {
    previewURL = URL.createObjectURL(el('video').files[0]);
    el('preview').src = previewURL; el('preview').hidden = false;
  } else { el('preview').hidden = true; el('preview').removeAttribute('src'); }
  el('consent').checked = false; requestKey = null;
});
for (const id of ['mode','privacy','branded','organic','commercial']) el(id).addEventListener('change', syncForm);
el('post-form').addEventListener('change', () => { if (!sending) { requestKey = null; el('consent').checked = false; } });
// Consent itself must remain selectable, while changing other settings invalidates it.
el('privacy').addEventListener('change', () => { el('public-confirm').checked = false; });
el('consent').addEventListener('change', event => event.stopPropagation());
function pollJob(id, attempts = 0) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      await loadJobs();
      if (!['PUBLISH_COMPLETE', 'FAILED', 'INIT_UNCERTAIN'].includes(job.phase) && attempts < 300) pollJob(id, attempts + 1);
      else tell(job.phase === 'PUBLISH_COMPLETE' ? 'TikTok gönderinin yayımlandığını bildirdi.' : `İş durumu: ${job.phase}`);
    } catch(error) { tell(error.message); if (attempts < 300) pollJob(id, attempts + 1); }
  }, 6000);
}
el('post-form').addEventListener('submit', async event => {
  event.preventDefault(); if (sending) return;
  sending = true; el('submit').disabled = true;
  requestKey ||= crypto.randomUUID();
  const data = new FormData(); data.set('video', el('video').files[0]);
  data.set('options', JSON.stringify({mode:el('mode').value, title:el('title').value, privacy_level:el('privacy').value || null,
    disable_comment:!el('comment').checked, disable_duet:!el('duet').checked, disable_stitch:!el('stitch').checked,
    brand_content_toggle:el('branded').checked, brand_organic_toggle:el('organic').checked, is_aigc:el('aigc').checked,
    consent:el('consent').checked, public_confirmed:el('public-confirm').checked}));
  try { const job = await api('/api/videos', {method:'POST', body:data, headers:{'Idempotency-Key':requestKey}}); tell(`Video gönderim işi: ${job.id} — ${job.phase}. Yayımlanma sonucunu durum kontrolünden izleyin.`); await loadJobs(); pollJob(job.id); }
  catch (error) { tell(error.message); await loadJobs().catch(() => {}); }
  finally { sending = false; syncForm(); }
});
for (const id of ['refresh','logout','disconnect']) el(id).addEventListener('click', async () => {
  if (id === 'disconnect' && !window.confirm('TikTok yetkisini kaldırıp bu hesabın yerel token ve iş kayıtlarını silelim mi?')) return;
  try { await api(id === 'refresh' ? '/api/token/refresh' : `/api/${id}`, {method:'POST'}); if (id === 'refresh') tell('Yetki yenilendi.'); else location.reload(); }
  catch(error) { tell(error.message); }
});
el('check-jobs').addEventListener('click', () => loadJobs(true).catch(error => tell(error.message)));
(async () => {
  try { const s = await api('/api/session'); csrf = s.csrf_token; allowPublic = s.allow_public_posts; el('studio').hidden = false; el('connect').textContent = 'TikTok hesabını yeniden bağla';
    el('generate').disabled = !s.nvidia_available;
    el('generation-note').textContent = s.nvidia_available ? 'Taslakları yayımlamadan önce doğrulayın. Sınırlar: 5/dakika, 20/gün/hesap, 100/gün/sunucu. Üretim video göndermez.' : 'NVIDIA içerik üretimi henüz yapılandırılmamış.';
    await loadJobs();
    try { await loadCreator(); } catch(error) {
      try { const user = await api('/api/profile'); el('creator').textContent = `TikTok hesabı: ${user.display_name}`; } catch(profileError) { tell(profileError.message); }
      tell(`${error.message}. Gelen kutusu taslağı için video.upload yetkisi ayrıca kullanılabilir.`); el('mode').value = 'inbox'; syncForm(); }
  } catch(error) { if (!error.message.includes('Connect your TikTok')) tell(error.message); }
})();

let generationKey = null, generating = false;
el('generation-form').addEventListener('input', () => { generationKey = null; });
el('generation-form').addEventListener('submit', async event => {
  event.preventDefault(); if (generating) return;
  generating = true; el('generate').disabled = true;
  generationKey ||= crypto.randomUUID();
  try {
    const result = await api('/api/content/generate', {method:'POST', headers:{'Content-Type':'application/json', 'Idempotency-Key':generationKey}, body:JSON.stringify({topic:el('generation-topic').value, category:el('generation-category').value, duration_seconds:Number(el('generation-duration').value), context:el('generation-context').value})});
    el('generated-script').value = result.script;
    el('generated-description').value = result.description;
    el('generated-titles').replaceChildren();
    for (const title of result.titles) { const item = document.createElement('li'); item.textContent = title; el('generated-titles').append(item); }
    el('generated-tags').textContent = result.hashtags.join(' ');
    el('generated-disclaimer').textContent = result.disclaimer;
    el('generation-result').hidden = false;
    tell('Taslak hazır. Bilgileri inceleyin; video gönderilmedi.');
  } catch(error) { tell(error.message); }
  finally { generating = false; el('generate').disabled = false; }
});
