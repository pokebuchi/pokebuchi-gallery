/* 写真を入れる画面 */

(function () {
  'use strict';

  var works = [];        // 作品リスト
  var tray = [];         // まだ割り当てていない写真（ブラウザの中だけ）
  var picked = null;     // クリックで選んでいる写真
  var tab = 'waiting';
  var packFilter = '';
  var seq = 0;

  var $ = function (id) { return document.getElementById(id); };

  /* ---------------------------------------------------- お知らせ */

  var toastTimer;
  function toast(msg, isError) {
    var t = $('toast');
    t.textContent = msg;
    t.className = 'toast' + (isError ? ' err' : '');
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, isError ? 6000 : 2600);
  }

  /* ---------------------------------------------------- 作品の読み込み */

  function load() {
    return fetch('/api/works')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        works = d.works || [];
        return fetch('/api/categories');
      })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        cats = d.categories || [];      // ギャラリーと同じ並び順
        buildPackFilter();
        render();
      });
  }

  // 絞り込みの並びも、カテゴリの並び順に合わせる
  function buildPackFilter() {
    var sel = $('pack-filter');
    var now = sel.value;
    var ある = {};
    works.forEach(function (w) { if (w.mainPack) ある[w.mainPack] = true; });

    sel.innerHTML = '<option value="">すべてのパック</option>';
    cats.forEach(function (c) {
      var 中身 = c.packs.filter(function (p) { return ある[p.name]; });
      if (!中身.length) return;

      var g = document.createElement('optgroup');
      g.label = c.name;
      中身.forEach(function (p) {
        var o = document.createElement('option');
        o.value = p.name;
        o.textContent = p.name;
        g.appendChild(o);
      });
      sel.appendChild(g);
    });

    // カテゴリに登録されていないパックがあれば、最後にまとめる
    var のこり = Object.keys(ある).filter(function (name) {
      return !cats.some(function (c) {
        return c.packs.some(function (p) { return p.name === name; });
      });
    });
    if (のこり.length) {
      var g2 = document.createElement('optgroup');
      g2.label = 'その他';
      のこり.forEach(function (name) {
        var o = document.createElement('option');
        o.value = name;
        o.textContent = name;
        g2.appendChild(o);
      });
      sel.appendChild(g2);
    }

    sel.value = now;
  }

  /* ---------------------------------------------------- 一覧の描画 */

  function visible() {
    return works.filter(function (w) {
      if (tab === 'waiting' && w.hasPhoto) return false;
      if (tab === 'done' && !w.hasPhoto) return false;
      if (packFilter && w.mainPack !== packFilter) return false;
      return true;
    });
  }

  function render() {
    var waiting = works.filter(function (w) { return !w.hasPhoto; }).length;
    $('n-waiting').textContent = waiting;
    $('n-done').textContent = works.length - waiting;

    // タブごとに、出すものを1つだけにする
    var 作品一覧 = (tab === 'waiting' || tab === 'done');
    $('cats').hidden = (tab !== 'cats');
    $('addbox').hidden = (tab !== 'add');
    $('works').hidden = !作品一覧;
    $('pack-filter').hidden = !作品一覧;
    $('tray').hidden = !作品一覧;       // 写真のドロップ欄も一緒に隠す
    if (tab === 'cats') renderCats();
    if (!作品一覧) {
      $('empty').hidden = true;
      return;
    }

    var list = visible();
    var box = $('works');
    box.innerHTML = '';

    $('empty').hidden = list.length > 0;
    $('empty').textContent = (tab === 'waiting')
      ? 'この条件の作品は、すべて写真が入っています。'
      : 'まだ写真を入れた作品がありません。';

    // 「入っている」タブでは、作品をタップすると直す画面がひらく
    $('done-hint').hidden = (tab !== 'done' || !list.length);

    var frag = document.createDocumentFragment();
    list.forEach(function (w) { frag.appendChild(card(w)); });
    box.appendChild(frag);
  }

  function card(w) {
    var el = document.createElement('button');
    el.type = 'button';
    el.className = 'work';
    el.dataset.i = w.i;

    var img = document.createElement('div');
    img.className = 'work-img';
    if (w.preview) {
      var im = document.createElement('img');
      im.src = w.preview + '?t=' + Date.now();
      im.alt = w.name;
      img.appendChild(im);
    } else {
      img.textContent = '＋';
    }

    var info = document.createElement('div');
    info.className = 'work-info';
    info.innerHTML =
      '<p class="work-name"></p><p class="work-pack"></p><p class="work-no"></p>';
    info.querySelector('.work-name').textContent = w.name;
    info.querySelector('.work-pack').textContent = w.packs.join(' / ');
    info.querySelector('.work-no').textContent = w.number;

    el.appendChild(img);
    el.appendChild(info);

    el.addEventListener('click', function () {
      if (picked !== null) {
        assign(tray[picked], w);       // 写真を選んでいるときは、それを入れる
      } else if (tab === 'done') {
        openEdit(w);                   // 入っているタブでは、直す画面をひらく
      } else {
        toast('先に上の写真をクリックして選んでください');
      }
    });

    el.addEventListener('dragover', function (e) {
      e.preventDefault();
      el.classList.add('over');
    });
    el.addEventListener('dragleave', function () { el.classList.remove('over'); });
    el.addEventListener('drop', function (e) {
      e.preventDefault();
      el.classList.remove('over');
      var id = e.dataTransfer.getData('text/plain');
      var item = tray.filter(function (x) { return String(x.id) === id; })[0];
      if (item) assign(item, w);
    });

    return el;
  }

  /* ---------------------------------------------------- 写真の受け取り */

  // ドロップされた写真は、その場でサーバーに預けてサムネイルを作ってもらう。
  // （HEIC はブラウザで表示できないため、こうしないと中身が見えない）
  function addFiles(files) {
    var 対象 = Array.prototype.filter.call(files, function (f) {
      var ext = (f.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
      return ['.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp'].indexOf(ext) !== -1;
    });

    if (!対象.length) {
      toast('写真として読める形式がありませんでした。\n' +
            'JPG・PNG・HEIC に対応しています。', true);
      return;
    }

    var 済 = 0, 失敗 = [];
    setBusy(true, '取り込んでいます … 0/' + 対象.length);

    // 1枚ずつ順に送る（まとめて送ると重い写真で詰まるため）
    function next(i) {
      if (i >= 対象.length) {
        setBusy(false);
        if (失敗.length) {
          toast(失敗.length + ' 枚が取り込めませんでした：\n' +
                失敗.slice(0, 3).join('\n'), true);
        } else {
          toast(済 + ' 枚を取り込みました');
        }
        return;
      }
      var f = 対象[i];
      var ext = (f.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
      var q = '?name=' + encodeURIComponent(f.name) + '&ext=' + encodeURIComponent(ext);

      fetch('/api/stage' + q, { method: 'POST', body: f })
        .then(function (r) {
          return r.json().then(function (d) { return { ok: r.ok, d: d }; });
        })
        .then(function (res) {
          if (!res.ok) throw new Error(res.d.error || f.name);
          tray.push({ id: res.d.id, name: res.d.name, preview: res.d.preview });
          済++;
          renderTray();
        })
        .catch(function (e) { 失敗.push(String(e.message || e)); })
        .then(function () {
          setBusy(true, '取り込んでいます … ' + (i + 1) + '/' + 対象.length);
          next(i + 1);
        });
    }
    next(0);
  }

  function setBusy(on, msg) {
    var d = $('drop');
    d.classList.toggle('busy', !!on);
    d.querySelector('.drop-main').textContent =
      on ? msg : 'ここに写真をまとめてドロップ';
  }

  function renderTray() {
    var body = $('tray-body');
    body.hidden = tray.length === 0;
    $('tray-count').textContent = tray.length;

    var list = $('tray-list');
    list.innerHTML = '';

    tray.forEach(function (item, idx) {
      var c = document.createElement('div');
      c.className = 'chip' + (picked === idx ? ' is-picked' : '');
      c.draggable = true;
      c.title = item.name;

      var im = document.createElement('img');
      im.src = item.preview;          // HEIC でもサーバーが作った画像で見える
      im.alt = item.name;
      c.appendChild(im);

      var cap = document.createElement('span');
      cap.className = 'cap';
      cap.textContent = item.name;
      c.appendChild(cap);

      var x = document.createElement('button');
      x.className = 'x';
      x.type = 'button';
      x.textContent = '×';
      x.title = 'この写真をどける';
      x.addEventListener('click', function (e) {
        e.stopPropagation();
        drop(idx);
      });
      c.appendChild(x);

      c.addEventListener('click', function () {
        picked = (picked === idx) ? null : idx;
        renderTray();
      });
      c.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', String(item.id));
      });

      list.appendChild(c);
    });
  }

  function drop(idx, サーバーからも消す) {
    var item = tray[idx];
    if (!item) return;
    if (サーバーからも消す !== false) {
      fetch('/api/unstage?id=' + encodeURIComponent(item.id), { method: 'POST' });
    }
    tray.splice(idx, 1);
    if (picked === idx) picked = null;
    else if (picked !== null && picked > idx) picked--;
    renderTray();
  }

  /* ---------------------------------------------------- 割り当て */

  function assign(item, w) {
    if (!item) return;
    var q = '?folder=' + encodeURIComponent(w.folder) +
            '&base=' + encodeURIComponent(w.base) +
            '&id=' + encodeURIComponent(item.id);

    fetch('/api/assign' + q, { method: 'POST' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.error || '入れられませんでした');
        w.hasPhoto = true;
        w.preview = res.d.preview;
        var i = tray.indexOf(item);
        if (i >= 0) drop(i, false);      // サーバー側では割り当て時に片づけ済み
        render();
        toast(w.name + ' に入れました');
      })
      .catch(function (e) { toast(e.message, true); });
  }

  /* ---------------------------------------------------- 作品を直す・消す */

  var 編集中 = null;

  function openEdit(w) {
    編集中 = w;
    $('edit-title').textContent = w.name + ' を直す';
    $('e-name').value = w.name;
    $('e-pack').value = w.mainPack || '';
    $('e-number').value = w.number || '';
    $('e-url').value = w.url || '';

    // 連結フレームはパック名とナンバーを使わない
    $('e-row-pack').hidden = w.isRenketsu;
    $('e-row-number').hidden = w.isRenketsu;
    $('e-note').textContent = w.hasPhoto
      ? '名前やパックを変えると、入れた写真も自動で付け替わります。'
      : 'まだ写真は入っていません。';

    $('edit').hidden = false;
  }

  function closeEdit() {
    $('edit').hidden = true;
    編集中 = null;
  }

  $('e-cancel').addEventListener('click', closeEdit);
  $('edit').addEventListener('click', function (e) {
    if (e.target === $('edit')) closeEdit();
  });

  $('e-save').addEventListener('click', function () {
    if (!編集中) return;
    var b = $('e-save');
    b.disabled = true;
    b.textContent = '保存しています …';

    fetch('/api/edit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        i: 編集中.i,
        name: $('e-name').value.trim(),
        pack: $('e-pack').value.trim(),
        number: $('e-number').value.trim(),
        url: $('e-url').value.trim()
      })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.error || '保存できませんでした');
        closeEdit();
        return load().then(function () {
          toast(res.d.name + ' を直しました' +
                (res.d.movedPhoto ? '（写真も付け替えました）' : ''));
        });
      })
      .catch(function (e) { toast(e.message, true); })
      .then(function () {
        b.disabled = false;
        b.textContent = '保存する';
      });
  });

  $('e-delete').addEventListener('click', function () {
    if (!編集中) return;
    if (!confirm('「' + 編集中.name + '」をギャラリーから削除します。\n' +
                 '入れた写真も一緒に消えます。よろしいですか？')) return;

    fetch('/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ i: 編集中.i })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.error || '削除できませんでした');
        closeEdit();
        return load().then(function () { toast(res.d.name + ' を削除しました'); });
      })
      .catch(function (e) { toast(e.message, true); });
  });

  /* ---------------------------------------------------- カテゴリの並び替え */

  var cats = [];      // 画面で編集中の並び。保存を押すまでファイルには書かない

  function loadCats() {
    return fetch('/api/categories')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        cats = d.categories || [];
        renderCats();
      });
  }

  function renderCats() {
    var box = $('cats-list');
    box.innerHTML = '';

    cats.forEach(function (c, ci) {
      var wrap = document.createElement('div');
      wrap.className = 'cat';

      var head = document.createElement('div');
      head.className = 'cat-head';
      head.innerHTML =
        '<span class="cat-name"></span><span class="cat-num"></span>' +
        '<span class="spacer"></span>';
      head.querySelector('.cat-name').textContent = c.name;
      head.querySelector('.cat-num').textContent = c.count + ' 作品';
      head.appendChild(動かすボタン('▲', ci === 0, function () {
        入れ替え(cats, ci, ci - 1); renderCats();
      }));
      head.appendChild(動かすボタン('▼', ci === cats.length - 1, function () {
        入れ替え(cats, ci, ci + 1); renderCats();
      }));
      wrap.appendChild(head);

      // 下位パックを持たないカテゴリ（連結フレーム）は、
      // パックのかわりに作品そのものを並べ替えられるようにする
      var 自前 = c.packs.filter(function (p) { return p.isSelf; }).length > 0;
      if (自前 && c.works && c.works.length) {
        c.works.forEach(function (w, wi) {
          var row = document.createElement('div');
          row.className = 'pack-row work-row';
          row.innerHTML = '<span class="pack-name"></span><span class="spacer"></span>';
          row.querySelector('.pack-name').textContent = w.name;
          row.appendChild(動かすボタン('▲', wi === 0, function () {
            入れ替え(c.works, wi, wi - 1); renderCats();
          }));
          row.appendChild(動かすボタン('▼', wi === c.works.length - 1, function () {
            入れ替え(c.works, wi, wi + 1); renderCats();
          }));
          wrap.appendChild(row);
        });
        box.appendChild(wrap);
        return;
      }

      c.packs.forEach(function (p, pi) {
        var row = document.createElement('div');
        row.className = 'pack-row' + (p.isSelf ? ' self' : '');
        row.innerHTML = '<span class="pack-name"></span><span class="pack-num"></span>' +
                        '<span class="spacer"></span>';
        row.querySelector('.pack-name').textContent = p.name;
        row.querySelector('.pack-num').textContent = p.count + ' 作品';

        // 別のカテゴリへ移す
        var sel = document.createElement('select');
        sel.className = 'moveto';
        var 頭 = document.createElement('option');
        頭.value = '';
        頭.textContent = '移動先…';
        sel.appendChild(頭);
        cats.forEach(function (other) {
          if (other.name === c.name) return;
          var o = document.createElement('option');
          o.value = other.name;
          o.textContent = other.name;
          sel.appendChild(o);
        });
        sel.addEventListener('change', function () {
          if (!sel.value) return;
          var 先 = cats.filter(function (x) { return x.name === sel.value; })[0];
          c.packs.splice(pi, 1);
          先.packs.push(p);
          数え直す();
          renderCats();
        });
        row.appendChild(sel);

        row.appendChild(動かすボタン('▲', pi === 0, function () {
          入れ替え(c.packs, pi, pi - 1); renderCats();
        }));
        row.appendChild(動かすボタン('▼', pi === c.packs.length - 1, function () {
          入れ替え(c.packs, pi, pi + 1); renderCats();
        }));

        wrap.appendChild(row);
      });

      box.appendChild(wrap);
    });
  }

  function 動かすボタン(記号, 使えない, 動き) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'mv';
    b.textContent = 記号;
    b.disabled = 使えない;
    b.addEventListener('click', 動き);
    return b;
  }

  function 入れ替え(配列, a, b) {
    var t = 配列[a];
    配列[a] = 配列[b];
    配列[b] = t;
  }

  function 数え直す() {
    cats.forEach(function (c) {
      c.count = c.packs.reduce(function (n, p) { return n + p.count; }, 0);
    });
  }

  $('cats-reload').addEventListener('click', function () {
    loadCats().then(function () { toast('元に戻しました'); });
  });

  $('cats-save').addEventListener('click', function () {
    var b = $('cats-save');
    b.disabled = true;
    b.textContent = '保存しています …';
    fetch('/api/categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ categories: cats })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.error || '保存できませんでした');
        toast('並び順を保存しました。「保存して公開する」で反映されます');
        return load();
      })
      .catch(function (e) { toast(e.message, true); })
      .then(function () {
        b.disabled = false;
        b.textContent = '並び順を保存する';
      });
  });

  /* ---------------------------------------------------- 作品を追加する */

  var 選択肢 = { categories: [], packs: {} };

  function loadChoices() {
    return fetch('/api/choices')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        選択肢 = d;
        var sel = $('f-category');
        sel.innerHTML = '';
        d.categories.forEach(function (c) {
          var o = document.createElement('option');
          o.value = c;
          o.textContent = c;
          sel.appendChild(o);
        });
        updatePackList();
      });
  }

  // 選んだカテゴリに合わせて、入力欄と候補を切り替える
  function updatePackList() {
    var cat = $('f-category').value;
    var 連結 = (cat === '連結フレーム');

    // 連結フレームはパック名もナンバーも使わないので、欄ごと隠す
    $('row-pack').hidden = 連結;
    $('row-number').hidden = 連結;
    $('renketsu-note').hidden = !連結;
    $('hint-name').textContent = 連結
      ? '「ケロマツ / ゲコガシラ / メガゲッコウガex」のように並べて書けます'
      : 'ギャラリーに表示される名前です';
    if (連結) {
      $('f-pack').classList.remove('bad');
      $('f-number').classList.remove('bad');
    }

    var dl = $('pack-list');
    dl.innerHTML = '';
    (選択肢.packs[cat] || []).forEach(function (p) {
      var o = document.createElement('option');
      o.value = p;
      dl.appendChild(o);
    });
  }

  function 入力欄をまっさらにする() {
    ['f-number', 'f-name', 'f-pack', 'f-url'].forEach(function (id) {
      $(id).value = '';
      $(id).classList.remove('bad');
    });
    return loadChoices();   // 中で updatePackList() が走り、欄の出し分けもされる
  }

  $('add-cancel').addEventListener('click', function () { タブへ('waiting'); });
  $('f-category').addEventListener('change', updatePackList);

  $('add-save').addEventListener('click', function () {
    var b = $('add-save');
    var 入力 = {
      number: $('f-number').value.trim(),
      name: $('f-name').value.trim(),
      pack: $('f-pack').value.trim(),
      category: $('f-category').value,
      url: $('f-url').value.trim()
    };

    // 足りない欄を赤くする（連結フレームはカード名だけでよい）
    var 連結 = (入力.category === '連結フレーム');
    var 必須 = 連結 ? [['f-name', 入力.name]]
                    : [['f-name', 入力.name], ['f-pack', 入力.pack],
                       ['f-number', 入力.number]];
    var 欠け = false;
    必須.forEach(function (x) {
      var 空 = !x[1];
      $(x[0]).classList.toggle('bad', 空);
      if (空) 欠け = true;
    });
    if (欠け) { toast('赤くなっている欄を埋めてください', true); return; }

    b.disabled = true;
    b.textContent = '追加しています …';

    fetch('/api/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(入力)
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.error || '追加できませんでした');
        $('pack-filter').value = '';
        packFilter = '';
        return load().then(function () {
          タブへ('waiting');            // 追加したら写真待ちに戻る
          toast(res.d.name + ' を写真待ちに追加しました');
        });
      })
      .catch(function (e) { toast(e.message, true); })
      .then(function () {
        b.disabled = false;
        b.textContent = '追加する';
      });
  });

  /* ---------------------------------------------------- 公開 */

  $('publish').addEventListener('click', function () {
    var b = $('publish');
    b.disabled = true;
    b.textContent = '保存しています …';
    fetch('/api/publish', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        toast(d.message || (d.ok ? '公開しました' : '保存しました'), !d.ok);
      })
      .catch(function (e) { toast(String(e), true); })
      .then(function () {
        b.disabled = false;
        b.textContent = '保存して公開する';
      });
  });

  /* ---------------------------------------------------- 画面のしかけ */

  var dz = $('drop');
  dz.addEventListener('click', function () { $('file').click(); });
  $('file').addEventListener('change', function (e) {
    addFiles(e.target.files);
    e.target.value = '';
  });

  ['dragenter', 'dragover'].forEach(function (t) {
    dz.addEventListener(t, function (e) { e.preventDefault(); dz.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach(function (t) {
    dz.addEventListener(t, function (e) { e.preventDefault(); dz.classList.remove('over'); });
  });
  dz.addEventListener('drop', function (e) {
    if (e.dataTransfer.files.length) {
      addFiles(e.dataTransfer.files);
    } else {
      webDropWarning(e.dataTransfer);
    }
  });

  // ブラウザで開いた Google フォトなどから直接ドラッグされた場合、
  // 届くのは写真そのものではなく「写真の住所」だけなので、その旨を案内する
  function webDropWarning(dt) {
    var types = Array.prototype.slice.call((dt && dt.types) || []);
    if (types.indexOf('text/uri-list') !== -1 || types.indexOf('text/html') !== -1) {
      toast('Webページからは直接入れられません。\n' +
            'いったんパソコンに保存してから、そのファイルをドロップしてください。', true);
    } else {
      toast('写真として読み取れませんでした', true);
    }
  }

  // 画面のどこに落としても、ブラウザが写真を開いてしまわないようにする
  ['dragover', 'drop'].forEach(function (t) {
    window.addEventListener(t, function (e) {
      if (!e.target.closest('.drop, .work')) e.preventDefault();
    });
  });

  $('tray-clear').addEventListener('click', function () {
    if (!tray.length) return;
    if (!confirm('取り込んだ ' + tray.length + ' 枚を、まだ割り当てていない状態で全部どけます。よろしいですか？')) return;
    fetch('/api/unstage-all', { method: 'POST' }).then(function () {
      tray = [];
      picked = null;
      renderTray();
    });
  });

  // 画面を開き直しても、取り込み済みの写真が残るようにする
  function loadStaged() {
    return fetch('/api/staged')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        tray = (d.staged || []).map(function (s) {
          return { id: s.id, name: s.name, preview: s.preview };
        });
        renderTray();
      });
  }

  // タブを切り替える。開けるのは常に1つだけ。
  function タブへ(名) {
    var 今 = document.querySelector('.tab.is-active');
    if (今) 今.classList.remove('is-active');
    var 次 = document.querySelector('.tab[data-tab="' + 名 + '"]');
    if (次) 次.classList.add('is-active');
    tab = 名;

    if (名 === 'cats') {
      loadCats().then(render);
    } else if (名 === 'add') {
      render();
      入力欄をまっさらにする().then(function () { $('f-number').focus(); });
    } else {
      render();
    }
  }

  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) {
    t.addEventListener('click', function () { タブへ(t.dataset.tab); });
  });

  $('pack-filter').addEventListener('change', function (e) {
    packFilter = e.target.value;
    render();
  });

  load().then(loadStaged);
})();
