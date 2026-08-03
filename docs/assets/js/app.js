/* ===========================================================
   Pokebuchi Gallery
   =========================================================== */

(function () {
  'use strict';

  var PAGE_SIZE = 30;

  var state = {
    works: [],
    series: [],
    filtered: [],
    shown: 0,
    category: '',      // MEGA / スカーレット＆バイオレット など、大きい方の分類
    pack: '',          // アビスアイ など、その中のパック
    query: '',
    lbIndex: -1
  };

  var el = {
    grid: document.getElementById('grid'),
    search: document.getElementById('search'),
    seriesList: document.getElementById('series-list'),
    packAll: document.querySelector('.pack-all'),
    packFilter: document.getElementById('pack-filter'),
    catToggle: document.getElementById('cat-toggle'),
    catCurrent: document.getElementById('cat-current'),
    resultCount: document.getElementById('result-count'),
    empty: document.getElementById('empty'),
    sentinel: document.getElementById('sentinel'),
    lb: document.getElementById('lightbox'),
    lbImg: document.getElementById('lb-img'),
    lbName: document.getElementById('lb-name'),
    lbPack: document.getElementById('lb-pack'),
    lbUrl: document.getElementById('lb-url'),
    lbPrev: document.getElementById('lb-prev'),
    lbNext: document.getElementById('lb-next'),
    lbClose: document.getElementById('lb-close'),
    lbHint: document.getElementById('lb-hint')
  };

  // 指で操作する端末かどうかで、案内の文言を変える
  var タッチ端末 = window.matchMedia('(pointer: coarse)').matches;

  /* ---------- 検索用の正規化 ---------------------------------
     全角/半角をそろえ、ひらがなをカタカナに寄せて、
     「でででんね」でも「デデンネ」でも当たるようにする。      */

  function normalize(s) {
    if (!s) return '';
    var t = s.normalize ? s.normalize('NFKC') : s;
    t = t.toLowerCase();
    // ひらがな → カタカナ
    t = t.replace(/[ぁ-ゖ]/g, function (c) {
      return String.fromCharCode(c.charCodeAt(0) + 0x60);
    });
    // 長音・記号・空白を無視
    return t.replace(/[\sー‐-―・･/／,、.。]/g, '');
  }

  /* ---------- URL ハッシュ（絞り込み状態の保存） ---------- */

  function readHash() {
    var h = location.hash.replace(/^#/, '');
    if (!h) return;
    h.split('&').forEach(function (part) {
      var i = part.indexOf('=');
      if (i < 0) return;
      var k = part.slice(0, i);
      var v = decodeURIComponent(part.slice(i + 1).replace(/\+/g, ' '));
      if (k === 'cat') state.category = v;
      if (k === 'pack') state.pack = v;
      if (k === 'q') state.query = v;
    });
  }

  function writeHash() {
    var parts = [];
    if (state.category) parts.push('cat=' + encodeURIComponent(state.category));
    if (state.pack) parts.push('pack=' + encodeURIComponent(state.pack));
    if (state.query) parts.push('q=' + encodeURIComponent(state.query));
    var next = parts.length ? '#' + parts.join('&') : ' ';
    if (next.trim() !== location.hash.replace(/^#/, '')) {
      history.replaceState(null, '', next === ' ' ? location.pathname : next);
    }
  }

  /* ---------- 絞り込み ---------- */

  // そのカテゴリに属するパック名の一覧。
  // 連結フレームのように下位パックを持たないものは、名前そのものが目印になる。
  function カテゴリのパック(名) {
    for (var i = 0; i < state.series.length; i++) {
      if (state.series[i].name === 名) {
        return state.series[i].packs.length ? state.series[i].packs : [名];
      }
    }
    return [];
  }

  function applyFilter() {
    var q = normalize(state.query);
    var 対象パック = state.category ? カテゴリのパック(state.category) : null;

    state.filtered = state.works.filter(function (w) {
      if (state.pack) {
        // パックまで選ばれていれば、それだけに絞る
        if (w.packs.indexOf(state.pack) === -1) return false;
      } else if (対象パック) {
        // カテゴリだけ選ばれていれば、その中のどれかに含まれていればよい
        var 当たり = false;
        for (var i = 0; i < 対象パック.length; i++) {
          if (w.packs.indexOf(対象パック[i]) !== -1) { 当たり = true; break; }
        }
        if (!当たり) return false;
      }
      if (q && w._search.indexOf(q) === -1) return false;
      return true;
    });
    state.shown = 0;
    el.grid.innerHTML = '';
    el.empty.hidden = state.filtered.length > 0;
    el.resultCount.textContent = state.filtered.length
      ? state.filtered.length + ' 点'
      : '';
    renderMore();
    updateFilterUI();
    writeHash();
  }

  function renderMore() {
    var next = state.filtered.slice(state.shown, state.shown + PAGE_SIZE);
    if (!next.length) return;

    var frag = document.createDocumentFragment();
    next.forEach(function (w) {
      frag.appendChild(buildCard(w, state.shown + next.indexOf(w)));
    });
    el.grid.appendChild(frag);
    state.shown += next.length;
  }

  function buildCard(w, index) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'card' + (w.soldOut ? ' is-sold' : '');
    btn.dataset.index = index;

    var thumb = document.createElement('div');
    thumb.className = 'card-thumb';   // 大きさは CSS で正方形に固定している

    var img = document.createElement('img');
    img.src = w.thumb;
    img.alt = w.name;
    img.loading = 'lazy';
    img.decoding = 'async';
    if (w.w) img.width = w.w;
    if (w.h) img.height = w.h;
    thumb.appendChild(img);

    if (w.soldOut) {
      var badge = document.createElement('span');
      badge.className = 'badge-sold';
      badge.textContent = 'SOLD OUT';
      thumb.appendChild(badge);
    }

    var name = document.createElement('p');
    name.className = 'card-name';
    name.textContent = w.name;

    btn.appendChild(thumb);
    btn.appendChild(name);
    return btn;
  }

  /* ---------- 絞り込みUIの生成 ---------- */

  function buildFilterUI() {
    state.series.forEach(function (s) {
      var packs = (s.packs || []).filter(function (p) { return countOf(p) > 0; });
      if (!packs.length && countOf(s.name) === 0) return;   // 作品が無い分類は出さない

      var wrap = document.createElement('div');
      wrap.className = 'series' + (packs.length ? '' : ' is-direct');
      wrap.dataset.series = s.name;

      var toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'series-toggle';
      toggle.innerHTML = '<span>' + s.name + '</span>' +
        (packs.length ? '<span class="chev">&#9660;</span>' : '');
      // 見出しを押した時点で、そのカテゴリ全体で絞り込む
      toggle.addEventListener('click', function () { selectCategory(s.name); });
      wrap.appendChild(toggle);

      if (packs.length) {
        var list = document.createElement('div');
        list.className = 'series-packs';
        packs.forEach(function (p) {
          var b = document.createElement('button');
          b.type = 'button';
          b.className = 'pack-btn';
          b.dataset.pack = p;
          b.textContent = p;
          b.addEventListener('click', function () { selectPack(s.name, p); });
          list.appendChild(b);
        });
        wrap.appendChild(list);
      }

      el.seriesList.appendChild(wrap);
    });
  }

  // カテゴリの見出しを押したとき
  function selectCategory(名) {
    if (state.category === 名 && !state.pack) {
      state.category = '';           // もう一度押したら解除
    } else {
      state.category = 名;
      state.pack = '';               // 中のパック指定は外す
    }
    applyFilter();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // カテゴリの中のパックを押したとき
  function selectPack(カテゴリ, p) {
    state.category = カテゴリ;
    state.pack = (state.pack === p) ? '' : p;
    applyFilter();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function countOf(pack) {
    var n = 0;
    for (var i = 0; i < state.works.length; i++) {
      if (state.works[i].packs.indexOf(pack) !== -1) n++;
    }
    return n;
  }

  function updateFilterUI() {
    // 選ばれているカテゴリだけを開き、見出しを太字にする
    var 各 = el.seriesList.querySelectorAll('.series');
    for (var i = 0; i < 各.length; i++) {
      各[i].classList.toggle('is-open', 各[i].dataset.series === state.category);
    }

    var btns = el.seriesList.querySelectorAll('.pack-btn');
    for (var j = 0; j < btns.length; j++) {
      btns[j].classList.toggle('is-active', btns[j].dataset.pack === state.pack);
    }

    el.packAll.classList.toggle('is-active', !state.category && !state.pack);

    // 閉じているときも、いま何で絞り込んでいるかが分かるようにする
    el.catCurrent.textContent = state.pack || state.category || 'すべて';
  }

  /* ---------- ライトボックス ---------- */

  function openLightbox(index) {
    var w = state.filtered[index];
    if (!w) return;
    state.lbIndex = index;

    el.lbImg.src = w.large || w.thumb;
    el.lbImg.alt = w.name;
    el.lbName.textContent = w.name;
    el.lbPack.textContent = w.packs.join(' / ');

    if (w.soldOut) {
      el.lbUrl.textContent = 'SOLD OUT';
      el.lbUrl.classList.add('is-sold');
      el.lbUrl.removeAttribute('href');
    } else {
      el.lbUrl.textContent = 'ショップへ移動';
      el.lbUrl.classList.remove('is-sold');
      el.lbUrl.href = w.url;
    }

    // 端の作品では、その方向の矢印を押せなくする
    el.lbPrev.classList.toggle('is-off', index <= 0);
    el.lbNext.classList.toggle('is-off', index >= state.filtered.length - 1);

    el.lbHint.textContent = (state.filtered.length > 1)
      ? (タッチ端末 ? '← 左右にスワイプしても移動できます →'
                    : '← → キーでも移動できます')
      : '';

    el.lb.hidden = false;
    document.body.classList.add('lb-open');
  }

  function closeLightbox() {
    el.lb.hidden = true;
    el.lbImg.src = '';
    document.body.classList.remove('lb-open');
    state.lbIndex = -1;
  }

  function step(delta) {
    var next = state.lbIndex + delta;
    if (next < 0 || next >= state.filtered.length) return;
    // まだ描画していない範囲へ進んだら追加で描画する
    while (next >= state.shown && state.shown < state.filtered.length) renderMore();
    openLightbox(next);
  }

  /* ---------- イベント ---------- */

  el.grid.addEventListener('click', function (e) {
    var card = e.target.closest('.card');
    if (card) openLightbox(Number(card.dataset.index));
  });

  el.search.addEventListener('input', function () {
    state.query = el.search.value;
    applyFilter();
  });

  el.packAll.addEventListener('click', function () {
    state.category = '';             // 開いているカテゴリもすべてたたむ
    state.pack = '';
    applyFilter();
  });

  // 「カテゴリ」の行を押すと、中身の出し入れをする
  el.catToggle.addEventListener('click', function () {
    var 開く = !el.packFilter.classList.contains('is-open');
    el.packFilter.classList.toggle('is-open', 開く);
    el.catToggle.setAttribute('aria-expanded', 開く ? 'true' : 'false');
  });

  el.lbClose.addEventListener('click', closeLightbox);
  el.lbPrev.addEventListener('click', function () { step(-1); });
  el.lbNext.addEventListener('click', function () { step(1); });

  el.lb.addEventListener('click', function (e) {
    if (e.target === el.lb || e.target.classList.contains('lb-figure')) closeLightbox();
  });

  document.addEventListener('keydown', function (e) {
    if (el.lb.hidden) return;
    if (e.key === 'Escape') closeLightbox();
    if (e.key === 'ArrowLeft') step(-1);
    if (e.key === 'ArrowRight') step(1);
  });

  // スワイプ
  var touchX = null;
  el.lb.addEventListener('touchstart', function (e) {
    touchX = e.changedTouches[0].clientX;
  }, { passive: true });
  el.lb.addEventListener('touchend', function (e) {
    if (touchX === null) return;
    var dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1);
    touchX = null;
  }, { passive: true });

  // 無限スクロール
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) renderMore();
    }, { rootMargin: '600px' }).observe(el.sentinel);
  }

  window.addEventListener('hashchange', function () {
    var before = state.category + '|' + state.pack + '|' + state.query;
    state.category = '';
    state.pack = '';
    state.query = '';
    readHash();
    if (before !== state.category + '|' + state.pack + '|' + state.query) {
      el.search.value = state.query;
      applyFilter();
    }
  });

  /* ---------- 起動 ---------- */

  fetch('data/works.json', { cache: 'no-cache' })
    .then(function (r) {
      if (!r.ok) throw new Error('works.json が読み込めません');
      return r.json();
    })
    .then(function (data) {
      state.series = data.series || [];
      state.works = (data.works || []).map(function (w) {
        w.packs = w.packs || [];
        w._search = normalize(w.name + w.packs.join(''));
        return w;
      });

      readHash();
      el.search.value = state.query;
      buildFilterUI();
      applyFilter();
    })
    .catch(function (err) {
      el.empty.hidden = false;
      el.empty.textContent = '作品を読み込めませんでした。（' + err.message + '）';
    });
})();
