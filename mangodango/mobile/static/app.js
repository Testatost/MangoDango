'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const DEFAULT_TEXTS = {
  library_title: 'Library',
  search: 'Search',
  sort_latest: 'Last updated',
  sort_az: 'A–Z',
  sort_favorites: 'Favorites',
  library_empty: 'No readable manga found.',
  chapter: 'Chapter',
  page: 'Page',
  start_question: 'Where do you want to open {manga}?',
  start_beginning: 'From beginning',
  start_continue: 'Continue reading',
  start_latest: 'Latest chapter',
  mode_strip: 'Long strip',
  mode_single: 'Single page',
  mode_double: 'Double page',
  previous: 'Previous',
  next: 'Next',
  refreshing: 'Refreshing…',
  first_chapter_reached: 'First chapter reached',
  last_chapter_reached: 'Latest chapter reached',
  menu_rename: 'Rename',
  menu_change_cover: 'Change cover',
  menu_favorite: 'Add to favorites',
  menu_unfavorite: 'Remove from favorites',
  menu_auto_add: 'Add to automatic updates',
  menu_auto_remove: 'Remove from automatic updates',
  menu_open_source: 'Open source page',
  menu_delete: 'Delete',
  rename_prompt: 'New title',
  delete_confirm: 'Delete {title}?',
  cancel: 'Cancel',
  preloading_pages: 'Loading pages… {done}/{total}',
  pages_ready: 'Pages ready',
  action_failed: 'Action failed: {error}',
};

const MAX_SESSION_CACHE_BYTES = 512 * 1024 * 1024;

const state = {
  library: [],
  manga: null,
  chapter: null,
  pageIndex: 0,
  readerMode: localStorage.getItem('mangodango-reader-mode') || 'strip',
  observer: null,
  language: 'en',
  texts: { ...DEFAULT_TEXTS },
  configSignature: '',
  chromeTimer: null,
  mangaCache: new Map(),
  progressCache: new Map(),
  warming: false,
  preloaded: new Set(),
  preloadPromises: new Map(),
  zoom: new WeakMap(),
  touch: null,
  lastTouchAt: 0,
  openToken: 0,
  progressTimer: null,
  objectUrls: new Map(),
  sessionPageCache: new Map(),
};

const views = {
  library: $('#libraryView'),
  manga: $('#mangaView'),
  reader: $('#readerView'),
};

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]));
}

function text(key) {
  return state.texts[key] || DEFAULT_TEXTS[key] || key;
}

function fmt(template, values) {
  return String(template || '').replace(/\{(\w+)\}/g, (_, key) => String(values?.[key] ?? `{${key}}`));
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove('show'), 2200);
}

async function api(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return response.json();
}

async function apiWrite(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-MangoDango-Request': '1' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return response.json();
}

async function apiUpload(url, file) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream', 'X-MangoDango-Request': '1' },
    body: file,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${response.status}`);
  }
  return response.json();
}


function applyI18n() {
  document.documentElement.lang = state.language;
  document.title = 'MangoDango';
  $('#libraryHeroTitle').textContent = text('library_title');
  $('#searchInput').placeholder = text('search');
  $('#sortLatest').textContent = text('sort_latest');
  $('#sortAz').textContent = text('sort_az');
  $('#sortFavorites').textContent = text('sort_favorites');
  $('#libraryEmpty').textContent = text('library_empty');
  $('#modeStrip').textContent = text('mode_strip');
  $('#modeSingle').textContent = text('mode_single');
  $('#modeDouble').textContent = text('mode_double');
  $('#backButton').setAttribute('aria-label', text('previous'));
  $('#prevPage').setAttribute('aria-label', text('previous'));
  $('#nextPage').setAttribute('aria-label', text('next'));
  $('#mangaMenuCancel').textContent = text('cancel');
  if (state.library.length) renderLibrary();
  if (state.manga && !views.manga.classList.contains('hidden')) renderManga();
  if (state.chapter) {
    $('#readerTitle').textContent = state.manga?.title || '';
    $('#readerSub').textContent = state.chapter.title;
    updateCounter();
  }
}

async function loadConfig() {
  try {
    const data = await api('/api/config');
    const signature = JSON.stringify(data);
    if (signature === state.configSignature) return;
    state.configSignature = signature;
    state.language = data.language || 'en';
    state.texts = { ...DEFAULT_TEXTS, ...(data.texts || {}) };
    applyI18n();
  } catch {
    // Keep the last valid translation table when the desktop app is temporarily busy.
  }
}

function showReaderChrome() {
  if (views.reader.classList.contains('hidden')) return;
  document.body.classList.remove('chrome-hidden');
  clearTimeout(state.chromeTimer);
  state.chromeTimer = setTimeout(() => document.body.classList.add('chrome-hidden'), 2000);
}

function show(name) {
  Object.entries(views).forEach(([key, view]) => view.classList.toggle('hidden', key !== name));
  $('#backButton').classList.toggle('hidden', name === 'library');
  $('#refreshButton').classList.toggle('hidden', name === 'reader');
  document.body.classList.toggle('reader-active', name === 'reader');
  if (name === 'reader') {
    document.body.classList.remove('chrome-hidden');
    showReaderChrome();
  } else {
    clearTimeout(state.chromeTimer);
    document.body.classList.remove('chrome-hidden');
    document.body.classList.remove('reader-mode-single');
    document.documentElement.classList.remove('reader-mode-double');
  }
  window.scrollTo(0, 0);
}

function legacyProgressKey(id) {
  return `mangodango-progress:${id}`;
}

function getProgress(id) {
  if (state.progressCache.has(id)) return state.progressCache.get(id);
  const manga = state.library.find((item) => item.id === id);
  return manga?.reading || null;
}

function latestChapterIsRead(manga, progress = null) {
  if (!manga) return false;
  const chapters = manga.chapters || [];
  if (!chapters.length) return Boolean(manga.latest_read);
  const latest = chapters[chapters.length - 1];
  const reading = progress || getProgress(manga.id) || manga.reading;
  if (!reading || !latest || !latest.pages) return false;
  const sameChapter = Boolean(reading.chapter_id && reading.chapter_id === latest.id)
    || (String(reading.chapter_title || '').trim().toLocaleLowerCase() === String(latest.title || '').trim().toLocaleLowerCase());
  if (!sameChapter) return false;
  const pageIndex = Number(reading.page_index);
  return Number.isFinite(pageIndex) && pageIndex >= Number(latest.pages) - 1;
}

function syncLatestReadFlag(manga, progress = null) {
  if (!manga) return false;
  const value = latestChapterIsRead(manga, progress);
  manga.latest_read = value;
  const libraryItem = state.library.find((item) => item.id === manga.id);
  if (libraryItem) {
    libraryItem.latest_read = value;
    if (progress) libraryItem.reading = progress;
  }
  return value;
}

async function loadProgressForManga(manga) {
  let progress = null;
  try {
    const data = await api(`/api/progress/${manga.id}`);
    progress = data.progress || null;
  } catch {
    progress = manga.reading || null;
  }

  if (!progress) {
    try {
      const legacy = JSON.parse(localStorage.getItem(legacyProgressKey(manga.id)) || 'null');
      if (legacy?.chapter_title) {
        const chapter = manga.chapters?.find((item) => item.id === legacy.chapter_id || item.title === legacy.chapter_title);
        if (chapter) {
          const migrated = await apiWrite(`/api/progress/${manga.id}`, {
            chapter_id: chapter.id,
            chapter_title: chapter.title,
            page_index: Number(legacy.page_index) || 0,
            reader_mode: state.readerMode,
          });
          progress = migrated.progress || null;
          localStorage.removeItem(legacyProgressKey(manga.id));
        }
      }
    } catch {
      // Legacy local progress is optional.
    }
  }

  if (progress) {
    state.progressCache.set(manga.id, progress);
    manga.reading = progress;
    manga.last_read_at = progress.updated_at || manga.last_read_at || 0;
    syncLatestReadFlag(manga, progress);
  }
  return progress;
}

function scheduleProgressSave() {
  clearTimeout(state.progressTimer);
  state.progressTimer = setTimeout(flushProgress, 180);
}

function saveProgress() {
  if (!state.manga || !state.chapter) return;
  const existing = getProgress(state.manga.id) || {};
  const progress = {
    ...existing,
    manga_id: state.manga.id,
    chapter_id: state.chapter.id,
    chapter_title: state.chapter.title,
    page_index: state.pageIndex,
    reader_mode: state.readerMode,
    updated_at: Date.now() / 1000,
  };
  state.progressCache.set(state.manga.id, progress);
  state.manga.reading = progress;
  state.manga.last_read_at = progress.updated_at;
  syncLatestReadFlag(state.manga, progress);
  updateCounter();
  scheduleProgressSave();
}

async function flushProgress() {
  clearTimeout(state.progressTimer);
  state.progressTimer = null;
  if (!state.manga || !state.chapter) return;
  const mangaId = state.manga.id;
  const progress = getProgress(mangaId);
  if (!progress) return;
  try {
    const data = await apiWrite(`/api/progress/${mangaId}`, {
      chapter_id: progress.chapter_id,
      chapter_title: progress.chapter_title,
      page_index: progress.page_index,
      reader_mode: progress.reader_mode || state.readerMode,
    });
    if (data.progress) {
      state.progressCache.set(mangaId, data.progress);
      if (state.manga?.id === mangaId) {
        state.manga.reading = data.progress;
        syncLatestReadFlag(state.manga, data.progress);
      }
    }
  } catch {
    // Progress is kept in memory and will be sent again after the next page change.
  }
}

function updateCounter() {
  const total = state.chapter?.pages || 0;
  $('#pageCounter').textContent = total
    ? `${text('page')} ${state.pageIndex + 1} / ${total}`
    : `${text('page')} 0 / 0`;
}

async function loadLibrary(force = false) {
  try {
    if (force) toast(text('refreshing'));
    const data = await api('/api/library');
    state.library = data.library || [];
    for (const manga of state.library) {
      if (manga.reading) state.progressCache.set(manga.id, manga.reading);
    }
    renderLibrary();
    warmMangaDetails();
  } catch (error) {
    $('#libraryGrid').innerHTML = `<div class="error">${esc(error.message)}</div>`;
  }
}

function renderLibrary() {
  const query = $('#searchInput').value.trim().toLocaleLowerCase();
  const sort = $('#sortSelect').value;
  let items = state.library.filter((manga) => !query || manga.title.toLocaleLowerCase().includes(query));
  if (sort === 'favorites') items = items.filter((manga) => manga.favorite);
  if (sort === 'az') {
    items.sort((a, b) => a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: 'base' }));
  } else {
    items.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0) || a.title.localeCompare(b.title));
  }
  $('#libraryEmpty').classList.toggle('hidden', items.length !== 0);
  $('#libraryGrid').innerHTML = items.map((manga) => {
    return `<button class="card" data-manga="${manga.id}">
      <div class="coverwrap">${manga.has_cover
        ? `<img class="cover" src="/api/cover/${manga.id}?v=${manga.cover_version || 0}" loading="lazy" alt="">`
        : '<div class="placeholder">M</div>'}</div>
      ${manga.favorite ? '<span class="star">★</span>' : ''}
      <div class="cardbody">
        <div class="cardtitle">${manga.latest_read ? '✓ ' : ''}${esc(manga.title)}</div>
        <div class="cardmeta">${manga.chapter_count} ${esc(text('chapter'))}${manga.latest_chapter ? ` · ${esc(text('start_latest'))}: ${esc(manga.latest_chapter)}` : ''}</div>
      </div>
    </button>`;
  }).join('');
  $$('[data-manga]').forEach((element) => element.addEventListener('click', () => openManga(element.dataset.manga)));
}

async function warmMangaDetails() {
  if (state.warming) return;
  const ids = state.library.map((manga) => manga.id).filter((id) => !state.mangaCache.has(id));
  if (!ids.length) return;
  state.warming = true;
  let cursor = 0;
  const worker = async () => {
    while (cursor < ids.length) {
      const id = ids[cursor++];
      try {
        state.mangaCache.set(id, await api(`/api/manga/${id}`));
      } catch {
        // Background warming is best effort only.
      }
    }
  };
  const run = () => Promise.all(Array.from({ length: Math.min(3, ids.length) }, worker))
    .finally(() => { state.warming = false; });
  if ('requestIdleCallback' in window) requestIdleCallback(() => run(), { timeout: 1000 });
  else setTimeout(() => run(), 100);
}

async function getMangaDetails(id) {
  const manga = state.mangaCache.get(id) || await api(`/api/manga/${id}`);
  state.mangaCache.set(manga.id, manga);
  await loadProgressForManga(manga);
  return manga;
}

async function openManga(id, push = true) {
  try {
    state.manga = await getMangaDetails(id);
    renderManga();
    show('manga');
    if (push) history.pushState({ view: 'manga', manga: state.manga.id }, '', `#manga=${state.manga.id}`);
  } catch (error) {
    toast(error.message);
  }
}

async function continueManga(id) {
  try {
    const manga = await getMangaDetails(id);
    state.manga = manga;
    const progress = getProgress(manga.id);
    const chapter = progress
      ? manga.chapters?.find((item) => item.id === progress.chapter_id || item.title === progress.chapter_title)
      : null;
    if (!chapter) {
      await openManga(id);
      return;
    }
    await openChapter(chapter.id, Math.max(0, Math.min(chapter.pages - 1, Number(progress.page_index) || 0)));
  } catch (error) {
    toast(error.message);
  }
}

function chapterCacheKey(mangaId, chapterId) {
  return `${mangaId}:${chapterId}`;
}

function renderManga() {
  const manga = state.manga;
  if (!manga) return;
  const progress = getProgress(manga.id);
  const first = manga.chapters?.[0];
  const latest = manga.chapters?.[manga.chapters.length - 1];
  const progressChapter = progress
    ? manga.chapters?.find((item) => item.id === progress.chapter_id || item.title === progress.chapter_title)
    : null;
  const question = fmt(text('start_question'), { manga: manga.title });

  $('#mangaHeader').innerHTML = `${manga.has_cover
    ? `<img class="detailcover" src="/api/cover/${manga.id}?v=${manga.cover_version || 0}" alt="">`
    : '<div class="detailcover placeholder">M</div>'}
    <div>
      <h1 class="detailtitle">${manga.latest_read ? '✓ ' : ''}${esc(manga.title)}</h1>
      <div class="detailmeta">${manga.chapter_count} ${esc(text('chapter'))}${manga.latest_chapter ? ` · ${esc(text('start_latest'))}: ${esc(manga.latest_chapter)}` : ''}</div>
      <div class="detailactions"><button id="mangaMenuButton" class="morebtn" aria-label="Menu">⋯</button></div>
      <div class="startquestion">${esc(question)}</div>
      <div class="startchoices">
        <button id="startBeginning" class="pillbtn accent" ${first ? '' : 'disabled'}>${esc(text('start_beginning'))}</button>
        <button id="startContinue" class="pillbtn" ${progressChapter ? '' : 'disabled'}>${esc(text('start_continue'))}</button>
        <button id="startLatest" class="pillbtn" ${latest ? '' : 'disabled'}>${esc(text('start_latest'))}</button>
      </div>
    </div>`;

  const chapters = [...(manga.chapters || [])].reverse();
  $('#chapterList').innerHTML = chapters.map((chapter) => `<button class="chapter" data-chapter="${chapter.id}">
      <div>
        <div class="chaptertitle">${esc(chapter.title)}</div>
        <div class="chaptermeta">${chapter.pages} ${esc(text('page'))}</div>
      </div>
      <span class="chev">›</span>
    </button>`).join('');

  $$('[data-chapter]').forEach((element) => element.addEventListener('click', () => openChapter(element.dataset.chapter, 0)));
  $('#startBeginning')?.addEventListener('click', () => first && openChapter(first.id, 0));
  $('#startLatest')?.addEventListener('click', () => latest && openChapter(latest.id, 0));
  $('#startContinue')?.addEventListener('click', () => {
    if (!progressChapter || !progress) return;
    openChapter(progressChapter.id, Math.max(0, Math.min(progressChapter.pages - 1, Number(progress.page_index) || 0)));
  });
  $('#mangaMenuButton')?.addEventListener('click', openMangaMenu);
}

function openMangaMenu() {
  const manga = state.manga;
  if (!manga) return;
  $('#mangaMenuTitle').textContent = manga.title;
  const actions = [
    ['rename', text('menu_rename')],
    ['cover', text('menu_change_cover')],
    ['favorite', manga.favorite ? text('menu_unfavorite') : text('menu_favorite')],
    ['auto', manga.check_updates ? text('menu_auto_remove') : text('menu_auto_add')],
    ['source', text('menu_open_source')],
    ['delete', text('menu_delete')],
  ];
  $('#mangaMenuActions').innerHTML = actions.map(([action, label]) => `<button class="sheetaction ${action === 'delete' ? 'danger' : ''}" data-menu-action="${action}" ${action === 'source' && !manga.source_url ? 'disabled' : ''}>${esc(label)}</button>`).join('');
  $$('[data-menu-action]').forEach((button) => button.addEventListener('click', () => performMangaAction(button.dataset.menuAction)));
  $('#mangaMenu').classList.remove('hidden');
}

function closeMangaMenu() {
  $('#mangaMenu').classList.add('hidden');
}



async function updateCurrentManga(data, oldId) {
  if (!data?.manga) return;
  state.manga = data.manga;
  if (oldId && oldId !== state.manga.id) {
    const progress = state.progressCache.get(oldId);
    if (progress) {
      state.progressCache.delete(oldId);
      state.progressCache.set(state.manga.id, { ...progress, manga_id: state.manga.id });
    }
    state.mangaCache.delete(oldId);
  }
  if (state.manga.reading) state.progressCache.set(state.manga.id, state.manga.reading);
  state.mangaCache.set(state.manga.id, state.manga);
  renderManga();
  history.replaceState({ view: 'manga', manga: state.manga.id }, '', `#manga=${state.manga.id}`);
  await loadLibrary(false);
}

async function performMangaAction(action) {
  const manga = state.manga;
  if (!manga) return;
  closeMangaMenu();
  try {
    if (action === 'source') {
      if (manga.source_url) window.open(manga.source_url, '_blank', 'noopener');
      return;
    }
    if (action === 'cover') {
      $('#coverFile').value = '';
      $('#coverFile').click();
      return;
    }
    if (action === 'rename') {
      const title = prompt(text('rename_prompt'), manga.title);
      if (!title || title.trim() === manga.title) return;
      const data = await apiWrite(`/api/manga/${manga.id}/action`, { action: 'rename', title: title.trim() });
      await updateCurrentManga(data, manga.id);
      return;
    }
    if (action === 'delete') {
      if (!confirm(fmt(text('delete_confirm'), { title: manga.title }))) return;
      await apiWrite(`/api/manga/${manga.id}/action`, { action: 'delete' });
      state.mangaCache.delete(manga.id);
      state.progressCache.delete(manga.id);
      state.manga = null;
      await loadLibrary(true);
      show('library');
      history.pushState({ view: 'library' }, '', '#');
      return;
    }
    const payload = { action };
    if (action === 'favorite') payload.value = !manga.favorite;
    else if (action === 'auto') payload.value = !manga.check_updates;
    const data = await apiWrite(`/api/manga/${manga.id}/action`, payload);
    await updateCurrentManga(data, manga.id);
  } catch (error) {
    toast(fmt(text('action_failed'), { error: error.message }));
  }
}

function pageUrl(manga, chapter, index) {
  return `/api/page/${manga.id}/${chapter.id}/${index}/page-${String(index + 1).padStart(4, '0')}.jpg`;
}

function setLoading(done, total) {
  $('#loadingText').textContent = fmt(text('preloading_pages'), { done, total });
  $('#loadingProgress').style.width = `${total ? Math.round(done * 100 / total) : 0}%`;
}

function showLoading(total) {
  setLoading(0, total);
  $('#loadingOverlay').classList.remove('hidden');
}

function hideLoading() {
  $('#loadingOverlay').classList.add('hidden');
}

function idbGet(url) {
  return Promise.resolve(state.sessionPageCache.get(url) || null);
}

function idbPut(record) {
  state.sessionPageCache.set(record.url, record);
  return Promise.resolve(true);
}


async function cachePage(url, mangaId, chapterId) {
  const existing = await idbGet(url);
  if (existing?.blob) return true;
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const blob = await response.blob();
  return idbPut({
    url,
    blob,
    size: Number(blob.size) || 0,
    mangaId,
    chapterId,
    chapterKey: chapterCacheKey(mangaId, chapterId),
    cachedAt: Date.now(),
  });
}

function preloadOneFallback(url) {
  return new Promise((resolve) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = resolve;
    image.onerror = resolve;
    image.src = url;
  });
}

async function enforceSessionCacheLimit(maxBytes = MAX_SESSION_CACHE_BYTES) {
  const records = [...state.sessionPageCache.values()];
  let total = records.reduce((sum, item) => sum + (Number(item.size) || 0), 0);
  if (total <= maxBytes) return;
  records.sort((a, b) => (a.cachedAt || 0) - (b.cachedAt || 0));
  for (const item of records) {
    if (total <= maxBytes) break;
    total -= Number(item.size) || 0;
    state.sessionPageCache.delete(item.url);
    const objectUrl = state.objectUrls.get(item.url);
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      state.objectUrls.delete(item.url);
    }
  }
}

async function preloadChapter(chapter, notify = true) {
  const manga = state.manga;
  if (!manga) return;
  const key = chapterCacheKey(manga.id, chapter.id);
  if (state.preloaded.has(key)) return;
  if (state.preloadPromises.has(key)) return state.preloadPromises.get(key);

  const promise = (async () => {
    const total = chapter.pages || 0;
    showLoading(total);
    let next = 0;
    let done = 0;
    const worker = async () => {
      while (next < total) {
        const index = next++;
        const url = pageUrl(manga, chapter, index);
        try {
          const stored = await cachePage(url, manga.id, chapter.id);
          if (!stored) await preloadOneFallback(url);
        } catch {
          await preloadOneFallback(url);
        }
        done += 1;
        setLoading(done, total);
      }
    };
    await Promise.all(Array.from({ length: Math.min(6, Math.max(1, total)) }, worker));
    state.preloaded.add(key);
    await enforceSessionCacheLimit();
    if (notify) toast(text('pages_ready'));
  })();

  state.preloadPromises.set(key, promise);
  try {
    await promise;
  } catch (error) {
    if (notify) toast(fmt(text('action_failed'), { error: error.message }));
  } finally {
    state.preloadPromises.delete(key);
    hideLoading();
  }
}


function releaseObjectUrls() {
  for (const objectUrl of state.objectUrls.values()) URL.revokeObjectURL(objectUrl);
  state.objectUrls.clear();
}


async function resolveCachedPageUrl(url) {
  if (state.objectUrls.has(url)) return state.objectUrls.get(url);
  const record = await idbGet(url);
  if (!record?.blob) return url;
  const objectUrl = URL.createObjectURL(record.blob);
  state.objectUrls.set(url, objectUrl);
  return objectUrl;
}

async function hydratePageImages() {
  const images = $$('img[data-page-url]');
  await Promise.all(images.map(async (image) => {
    const original = image.dataset.pageUrl;
    if (!original) return;
    image.src = await resolveCachedPageUrl(original);
  }));
}

function pageFrame(index) {
  const url = pageUrl(state.manga, state.chapter, index);
  return `<div class="pageframe" data-index="${index}"><img class="page" data-index="${index}" data-page-url="${esc(url)}" loading="eager" decoding="async" draggable="false" alt="${esc(text('page'))} ${index + 1}"></div>`;
}

async function openChapter(chapterId, startPage = 0, push = true) {
  const chapter = state.manga?.chapters?.find((item) => item.id === chapterId);
  if (!chapter) return;
  const token = ++state.openToken;
  await preloadChapter(chapter, false);
  if (token !== state.openToken) return;
  releaseObjectUrls();
  state.chapter = chapter;
  state.pageIndex = Math.max(0, Math.min(chapter.pages - 1, startPage || 0));
  $('#readerTitle').textContent = state.manga.title;
  $('#readerSub').textContent = chapter.title;
  $('#modeSelect').value = state.readerMode;
  renderReaderPages(false);
  show('reader');
  requestAnimationFrame(() => scrollToCurrent(false));
  if (push) history.pushState({ view: 'reader', manga: state.manga.id, chapter: chapter.id, page: state.pageIndex }, '', `#read=${state.manga.id}/${chapter.id}/${state.pageIndex}`);
  saveProgress();
}

function disconnectObserver() {
  if (state.observer) {
    state.observer.disconnect();
    state.observer = null;
  }
}

function setupReaderObserver() {
  disconnectObserver();
  if (state.readerMode === 'single') return;
  const selector = state.readerMode === 'double' ? '.spread' : '.pageframe';
  state.observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    const index = Number(visible.target.dataset.index) || 0;
    if (index !== state.pageIndex) {
      state.pageIndex = index;
      saveProgress();
    }
  }, { threshold: [0.25, 0.5, 0.75] });
  $$(selector).forEach((element) => state.observer.observe(element));
}

function renderReaderPages(scroll = true) {
  const pages = $('#pages');
  pages.className = `pages ${state.readerMode}`;
  state.zoom = new WeakMap();
  if (state.readerMode === 'double') {
    let html = '';
    for (let index = 0; index < state.chapter.pages; index += 2) {
      html += `<section class="spread ${index + 1 >= state.chapter.pages ? 'single-last' : ''}" data-index="${index}"><div class="spread-inner">${pageFrame(index)}${index + 1 < state.chapter.pages ? pageFrame(index + 1) : ''}</div></section>`;
    }
    pages.innerHTML = html;
  } else {
    pages.innerHTML = Array.from({ length: state.chapter.pages }, (_, index) => pageFrame(index)).join('');
  }
  document.body.classList.toggle('reader-mode-single', state.readerMode === 'single');
  document.documentElement.classList.toggle('reader-mode-double', state.readerMode === 'double');
  if (state.readerMode === 'single') renderSinglePage();
  setupReaderObserver();
  hydratePageImages();
  if (scroll) requestAnimationFrame(() => scrollToCurrent(false));
}

function renderSinglePage() {
  $$('.pages.single .pageframe').forEach((element) => element.classList.toggle('active', Number(element.dataset.index) === state.pageIndex));
  saveProgress();
}

function scrollToCurrent(smooth = true) {
  if (!state.chapter) return;
  let target = null;
  if (state.readerMode === 'double') {
    const start = Math.floor(state.pageIndex / 2) * 2;
    target = document.querySelector(`.spread[data-index="${start}"]`);
  } else if (state.readerMode === 'strip') {
    target = document.querySelector(`.pageframe[data-index="${state.pageIndex}"]`);
  }
  if (target) target.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
}

function applyReaderMode(scroll = true) {
  state.readerMode = $('#modeSelect').value;
  localStorage.setItem('mangodango-reader-mode', state.readerMode);
  renderReaderPages(scroll);
  saveProgress();
}

function changePage(delta) {
  if (!state.chapter) return;
  if (state.readerMode === 'strip') {
    const next = state.pageIndex + delta;
    if (next >= state.chapter.pages) return changeChapter(1);
    if (next < 0) return changeChapter(-1, true);
    state.pageIndex = next;
    scrollToCurrent(true);
    saveProgress();
    return;
  }
  if (state.readerMode === 'single') {
    const next = state.pageIndex + delta;
    if (next >= state.chapter.pages) return changeChapter(1);
    if (next < 0) return changeChapter(-1, true);
    state.pageIndex = next;
    renderSinglePage();
    return;
  }
  const current = Math.floor(state.pageIndex / 2) * 2;
  const next = current + delta * 2;
  if (next >= state.chapter.pages) return changeChapter(1);
  if (next < 0) return changeChapter(-1, true);
  state.pageIndex = next;
  scrollToCurrent(false);
  saveProgress();
}

async function changeChapter(delta, toEnd = false) {
  const list = state.manga?.chapters || [];
  const index = list.findIndex((chapter) => chapter.id === state.chapter?.id);
  const target = list[index + delta];
  if (!target) {
    toast(delta > 0 ? text('last_chapter_reached') : text('first_chapter_reached'));
    return;
  }
  await openChapter(target.id, toEnd ? Math.max(0, target.pages - 1) : 0, false);
  history.replaceState({ view: 'reader', manga: state.manga.id, chapter: target.id, page: state.pageIndex }, '', `#read=${state.manga.id}/${target.id}/${state.pageIndex}`);
}

function goBack() {
  flushProgress();
  if (!views.reader.classList.contains('hidden')) {
    state.openToken += 1;
    renderManga();
    show('manga');
    history.pushState({ view: 'manga', manga: state.manga.id }, '', `#manga=${state.manga.id}`);
    return;
  }
  if (!views.manga.classList.contains('hidden')) {
    renderLibrary();
    show('library');
    history.pushState({ view: 'library' }, '', '#');
  }
}

function goHome() {
  flushProgress();
  state.openToken += 1;
  show('library');
  history.pushState({ view: 'library' }, '', '#');
  loadLibrary(false);
}

async function restoreHash() {
  const hash = location.hash.slice(1);
  try {
    if (hash.startsWith('read=')) {
      const [mangaId, chapterId, page = '0'] = hash.slice(5).split('/');
      state.manga = await getMangaDetails(mangaId);
      await openChapter(chapterId, Number(page) || 0, false);
      return;
    }
    if (hash.startsWith('manga=')) {
      await openManga(hash.slice(6), false);
      return;
    }
  } catch {
    // Fall through to the library.
  }
  renderLibrary();
  show('library');
}

function zoomTargetFromElement(element) {
  if (state.readerMode === 'double') return element.closest('.spread')?.querySelector('.spread-inner') || null;
  return element.closest('.page');
}

function zoomViewport(target) {
  return target?.classList.contains('spread-inner') ? target.closest('.spread') : target?.closest('.pageframe');
}

function zoomFor(target) {
  let zoom = state.zoom.get(target);
  if (!zoom) {
    zoom = { scale: 1, x: 0, y: 0 };
    state.zoom.set(target, zoom);
  }
  return zoom;
}

function applyZoom(target, zoom) {
  const frame = zoomViewport(target);
  if (!frame) return;
  zoom.scale = Math.max(1, Math.min(4, zoom.scale));
  const maxX = Math.max(0, (target.clientWidth * zoom.scale - frame.clientWidth) / 2);
  const maxY = Math.max(0, (target.clientHeight * zoom.scale - frame.clientHeight) / 2);
  zoom.x = Math.max(-maxX, Math.min(maxX, zoom.x));
  zoom.y = Math.max(-maxY, Math.min(maxY, zoom.y));
  target.style.transform = `translate3d(${zoom.x}px,${zoom.y}px,0) scale(${zoom.scale})`;
}

function touchDistance(touches) {
  return Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
}

function touchMidpoint(touches) {
  return {
    x: (touches[0].clientX + touches[1].clientX) / 2,
    y: (touches[0].clientY + touches[1].clientY) / 2,
  };
}

$('#pages').addEventListener('touchstart', (event) => {
  const target = zoomTargetFromElement(event.target);
  if (!target) return;
  state.lastTouchAt = Date.now();
  if (event.touches.length === 2) {
    const zoom = zoomFor(target);
    const midpoint = touchMidpoint(event.touches);
    state.touch = {
      type: 'pinch', target,
      startDistance: Math.max(1, touchDistance(event.touches)),
      startScale: zoom.scale,
      startX: zoom.x, startY: zoom.y,
      startMid: midpoint,
    };
    event.preventDefault();
    return;
  }
  if (event.touches.length === 1) {
    const touch = event.touches[0];
    const zoom = zoomFor(target);
    state.touch = {
      type: 'single', target,
      startX: touch.clientX, startY: touch.clientY,
      lastX: touch.clientX, lastY: touch.clientY,
      startTime: performance.now(),
      zoomScale: zoom.scale, zoomX: zoom.x, zoomY: zoom.y,
      panned: false,
    };
  }
}, { passive: false });

$('#pages').addEventListener('touchmove', (event) => {
  const gesture = state.touch;
  if (!gesture) return;
  if (gesture.type === 'pinch' && event.touches.length >= 2) {
    const zoom = zoomFor(gesture.target);
    const midpoint = touchMidpoint(event.touches);
    zoom.scale = gesture.startScale * (touchDistance(event.touches) / gesture.startDistance);
    zoom.x = gesture.startX + (midpoint.x - gesture.startMid.x);
    zoom.y = gesture.startY + (midpoint.y - gesture.startMid.y);
    applyZoom(gesture.target, zoom);
    event.preventDefault();
    return;
  }
  if (gesture.type === 'single' && event.touches.length === 1) {
    const zoom = zoomFor(gesture.target);
    const touch = event.touches[0];
    gesture.lastX = touch.clientX;
    gesture.lastY = touch.clientY;
    if (zoom.scale > 1.01) {
      zoom.x = gesture.zoomX + (touch.clientX - gesture.startX);
      zoom.y = gesture.zoomY + (touch.clientY - gesture.startY);
      applyZoom(gesture.target, zoom);
      gesture.panned = true;
      event.preventDefault();
    }
  }
}, { passive: false });

$('#pages').addEventListener('touchend', (event) => {
  const gesture = state.touch;
  if (!gesture) return;
  if (gesture.type === 'pinch') {
    if (event.touches.length === 0) state.touch = null;
    return;
  }
  if (event.touches.length > 0) return;
  state.touch = null;
  const dx = gesture.lastX - gesture.startX;
  const dy = gesture.lastY - gesture.startY;
  const distance = Math.hypot(dx, dy);
  const duration = performance.now() - gesture.startTime;
  const zoom = zoomFor(gesture.target);
  if (!gesture.panned && zoom.scale <= 1.01 && state.readerMode === 'single' && duration < 850) {
    if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.25) {
      dx > 0 ? changePage(1) : changePage(-1);
      return;
    }
    if (Math.abs(dy) > 55 && Math.abs(dy) > Math.abs(dx) * 1.25) {
      dy < 0 ? changePage(1) : changePage(-1);
      return;
    }
  }
  if (distance < 12 && duration < 650) showReaderChrome();
}, { passive: true });

$('#pages').addEventListener('click', (event) => {
  if (Date.now() - state.lastTouchAt < 700) return;
  if (event.target.closest('.page')) showReaderChrome();
});

$('#pages').addEventListener('dblclick', (event) => {
  const target = zoomTargetFromElement(event.target);
  if (!target) return;
  const zoom = zoomFor(target);
  zoom.scale = zoom.scale > 1.01 ? 1 : 2;
  zoom.x = 0;
  zoom.y = 0;
  applyZoom(target, zoom);
});

$('#coverFile').addEventListener('change', async () => {
  const file = $('#coverFile').files?.[0];
  const manga = state.manga;
  if (!file || !manga) return;
  try {
    const data = await apiUpload(`/api/manga/${manga.id}/cover`, file);
    await updateCurrentManga(data, manga.id);
  } catch (error) {
    toast(fmt(text('action_failed'), { error: error.message }));
  }
});

$('#mangaMenuCancel').addEventListener('click', closeMangaMenu);
$('#mangaMenu').addEventListener('click', (event) => {
  if (event.target === $('#mangaMenu')) closeMangaMenu();
});
$('#searchInput').addEventListener('input', renderLibrary);
$('#sortSelect').addEventListener('change', renderLibrary);
$('#refreshButton').addEventListener('click', () => loadLibrary(true));
$('#backButton').addEventListener('click', goBack);
$('#homeBrand').addEventListener('click', goHome);
$('#modeSelect').addEventListener('change', () => {
  applyReaderMode();
  showReaderChrome();
});
$('#prevPage').addEventListener('click', () => changePage(-1));
$('#nextPage').addEventListener('click', () => changePage(1));

addEventListener('keydown', (event) => {
  if (views.reader.classList.contains('hidden')) return;
  if (event.key === 'ArrowLeft' || event.key === 'PageUp') changePage(-1);
  if (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ') changePage(1);
});

let viewportRealignTimer = null;
function realignReaderViewport() {
  if (!state.chapter) return;
  clearTimeout(viewportRealignTimer);
  viewportRealignTimer = setTimeout(() => {
    if (state.readerMode === 'single') {
      renderSinglePage();
      return;
    }
    if (state.readerMode === 'double') scrollToCurrent(false);
  }, 90);
}

addEventListener('resize', realignReaderViewport);
addEventListener('orientationchange', realignReaderViewport);
addEventListener('popstate', restoreHash);
addEventListener('pagehide', flushProgress);

(async () => {
  await loadConfig();
  await loadLibrary();
  await restoreHash();
  setInterval(loadConfig, 2000);
})();
