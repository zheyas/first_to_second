(function () {
  "use strict";

  const state = { data: null, granularity: "day" };
  let openPopup = null;

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function fmtInt(n) { return (n || 0).toLocaleString("ru-RU"); }
  function fmtPct(n) { return `${(n ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`; }
  function ruDate(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-");
    return `${d}.${m}.${y}`;
  }

  function badgeColor(value) {
    const ranges = state.data && state.data.colors || [];
    for (const r of ranges) if (value >= r.low && value <= r.high) return r.color;
    return "#dfe4ea";
  }

  function convBadge(value) {
    const span = document.createElement("span");
    span.className = "badge";
    span.style.background = badgeColor(value);
    span.textContent = fmtPct(value);
    return span;
  }

  // ---------- generic sortable / filterable table ----------
  function makeTable(container, { columns, filterKey, defaultSort = { key: "first", dir: "desc" } }) {
    let rawRows = [];
    let sort = { ...defaultSort };
    let filterValues = null; // null = all

    function uniqueValues() {
      return Array.from(new Set(rawRows.map((r) => r[filterKey]))).sort((a, b) => a.localeCompare(b, "ru"));
    }

    function visibleRows() {
      let rows = rawRows;
      if (filterValues) rows = rows.filter((r) => filterValues.has(r[filterKey]));
      const col = columns.find((c) => c.key === sort.key);
      const dir = sort.dir === "asc" ? 1 : -1;
      return [...rows].sort((a, b) => {
        let av = a[sort.key], bv = b[sort.key];
        if (col && col.numeric) { av = av ?? -Infinity; bv = bv ?? -Infinity; return (av - bv) * dir; }
        return String(av ?? "").localeCompare(String(bv ?? ""), "ru") * dir;
      });
    }

    function totalsRow(rows) {
      const first = rows.reduce((s, r) => s + (r.first || 0), 0);
      const second = rows.reduce((s, r) => s + (r.second || 0), 0);
      const daysRows = rows.filter((r) => r.avg_days != null && r.second);
      const avg_days = daysRows.length
        ? Math.round((daysRows.reduce((s, r) => s + r.avg_days * r.second, 0) / (second || 1)) * 10) / 10
        : null;
      return { name: "Итого", first, second, conversion: first ? Math.round((second / first) * 1000) / 10 : 0, avg_days };
    }

    function closePopup() {
      if (openPopup) { openPopup.remove(); openPopup = null; }
    }

    function openFilterPopup(anchor) {
      if (openPopup) { closePopup(); return; }
      const pop = document.createElement("div");
      pop.className = "filter-pop";
      const values = uniqueValues();
      const selected = filterValues || new Set(values);
      pop.innerHTML = `
        <input type="search" placeholder="Поиск…">
        <div class="list"></div>
        <div class="actions">
          <button data-act="all">Выбрать все</button>
          <button data-act="reset">Сбросить</button>
        </div>`;
      const list = $(".list", pop);
      function renderList(filterText) {
        list.innerHTML = "";
        const ft = (filterText || "").toLowerCase();
        values.filter((v) => v.toLowerCase().includes(ft)).forEach((v) => {
          const id = "f_" + Math.random().toString(36).slice(2);
          const label = document.createElement("label");
          label.innerHTML = `<input type="checkbox" id="${id}"> <span></span>`;
          label.querySelector("span").textContent = v;
          const cb = label.querySelector("input");
          cb.checked = selected.has(v);
          cb.addEventListener("change", () => {
            if (cb.checked) selected.add(v); else selected.delete(v);
            filterValues = selected.size === values.length ? null : new Set(selected);
            render();
            anchor.classList.toggle("is-active", !!filterValues);
          });
          list.appendChild(label);
        });
      }
      renderList("");
      $("input[type=search]", pop).addEventListener("input", (e) => renderList(e.target.value));
      pop.querySelector('[data-act="all"]').addEventListener("click", () => {
        filterValues = null; anchor.classList.remove("is-active"); render(); closePopup();
      });
      pop.querySelector('[data-act="reset"]').addEventListener("click", () => {
        filterValues = new Set(); anchor.classList.add("is-active"); render(); closePopup();
      });
      document.body.appendChild(pop);
      const rect = anchor.getBoundingClientRect();
      pop.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - 340)}px`;
      pop.style.left = `${Math.min(rect.left, window.innerWidth - 250)}px`;
      openPopup = pop;
      setTimeout(() => document.addEventListener("click", onOutside), 0);
      function onOutside(e) {
        if (!pop.contains(e.target) && e.target !== anchor) { closePopup(); document.removeEventListener("click", onOutside); }
      }
    }

    function renderHead() {
      const tr = document.createElement("tr");
      columns.forEach((c) => {
        const th = document.createElement("th");
        if (c.numeric) th.classList.add("is-num");
        const inner = document.createElement("span");
        inner.className = "th-inner";
        const label = document.createElement("span");
        label.textContent = c.label;
        inner.appendChild(label);
        const arrow = document.createElement("span");
        arrow.className = "arrow" + (sort.key === c.key ? " is-active" : "");
        arrow.textContent = sort.key === c.key ? (sort.dir === "asc" ? "▲" : "▼") : "▲▼";
        inner.appendChild(arrow);
        if (c.key === filterKey) {
          const icon = document.createElement("span");
          icon.className = "filter-icon" + (filterValues ? " is-active" : "");
          icon.textContent = "▾";
          icon.addEventListener("click", (e) => { e.stopPropagation(); openFilterPopup(icon); });
          inner.appendChild(icon);
        }
        th.appendChild(inner);
        th.addEventListener("click", (e) => {
          if (e.target.classList.contains("filter-icon")) return;
          if (sort.key === c.key) sort.dir = sort.dir === "asc" ? "desc" : "asc";
          else sort = { key: c.key, dir: c.numeric ? "desc" : "asc" };
          render();
        });
        tr.appendChild(th);
      });
      return tr;
    }

    function renderRow(row, isTotal) {
      const tr = document.createElement("tr");
      if (isTotal) tr.className = "is-total";
      columns.forEach((c) => {
        const td = document.createElement("td");
        if (c.numeric) td.classList.add("is-num");
        if (c.render) td.appendChild(c.render(row));
        else td.textContent = c.format ? c.format(row[c.key], row) : row[c.key];
        tr.appendChild(td);
      });
      return tr;
    }

    function render() {
      container.innerHTML = "";
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      thead.appendChild(renderHead());
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      const rows = visibleRows();
      if (!rows.length) {
        const tr = document.createElement("tr");
        tr.className = "empty-row";
        const td = document.createElement("td");
        td.colSpan = columns.length;
        td.textContent = "Нет данных за выбранный период.";
        tr.appendChild(td);
        tbody.appendChild(tr);
      } else {
        rows.forEach((r) => tbody.appendChild(renderRow(r, false)));
        tbody.appendChild(renderRow(totalsRow(rows), true));
      }
      table.appendChild(tbody);
      container.appendChild(table);
    }

    return {
      setData(rows) { rawRows = rows || []; filterValues = null; render(); },
    };
  }

  const sliceColumns = (nameLabel) => [
    { key: "name", label: nameLabel, numeric: false },
    { key: "first", label: "1‑й абонемент", numeric: true, format: fmtInt },
    { key: "second", label: "2‑й абонемент", numeric: true, format: fmtInt },
    { key: "conversion", label: "Конверсия", numeric: true, render: (r) => convBadge(r.conversion) },
    { key: "avg_days", label: "Дней до 2‑й покупки", numeric: true, format: (v) => (v == null ? "—" : v) },
  ];

  const tables = {};

  function initTables() {
    tables.cities = makeTable($("#citiesTable"), { columns: sliceColumns("Город"), filterKey: "name" });
    tables.branches = makeTable($("#branchesTable"), { columns: sliceColumns("Филиал"), filterKey: "name" });
    tables.managers = makeTable($("#managersTable"), { columns: sliceColumns("Менеджер"), filterKey: "name" });
    tables.trainers = makeTable($("#trainersTable"), { columns: sliceColumns("Тренер"), filterKey: "name" });
    tables.dyn = makeTable($("#dynTable"), {
      columns: [
        { key: "name", label: "Период", numeric: false },
        { key: "first", label: "1‑й абонемент", numeric: true, format: fmtInt },
        { key: "second", label: "2‑й абонемент", numeric: true, format: fmtInt },
        { key: "conversion", label: "Конверсия", numeric: true, render: (r) => convBadge(r.conversion) },
      ],
      filterKey: null,
      defaultSort: { key: "name", dir: "asc" },
    });
  }

  // ---------- dynamics: group daily rows into weeks/months ----------
  function weekStartISO(iso) {
    const d = new Date(iso + "T00:00:00");
    const day = (d.getDay() + 6) % 7; // Monday=0
    d.setDate(d.getDate() - day);
    return d.toISOString().slice(0, 10);
  }

  function groupDaily(daily, granularity) {
    if (granularity === "day") return daily.map((d) => ({ name: ruDate(d.date), sortKey: d.date, first: d.first, second: d.second }));
    const buckets = new Map();
    for (const d of daily) {
      const key = granularity === "week" ? weekStartISO(d.date) : d.date.slice(0, 7);
      const b = buckets.get(key) || { first: 0, second: 0 };
      b.first += d.first; b.second += d.second;
      buckets.set(key, b);
    }
    return Array.from(buckets.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, b]) => ({
        name: granularity === "week" ? `нед. ${ruDate(key)}` : key,
        sortKey: key,
        first: b.first, second: b.second,
      }));
  }

  function renderChart(rows) {
    const el = $("#dynChart");
    el.innerHTML = "";
    if (!rows.length) { el.innerHTML = '<div class="chart__empty">Нет данных за выбранный период.</div>'; return; }
    const max = Math.max(1, ...rows.map((r) => r.first));
    rows.forEach((r) => {
      const wrap = document.createElement("div");
      wrap.className = "chart__bar-wrap";
      const stack = document.createElement("div");
      stack.className = "chart__bar-stack";
      stack.style.height = `${Math.max(2, (r.first / max) * 130)}px`;
      const conv = r.first ? Math.round((r.second / r.first) * 1000) / 10 : 0;
      const tip = `${r.name}\n1‑й: ${r.first}  2‑й: ${r.second}\nконверсия: ${conv}%`;

      const bottom = document.createElement("div");
      bottom.className = "chart__bar chart__bar--second";
      bottom.style.height = r.first ? `${(r.second / r.first) * 100}%` : "0%";
      bottom.dataset.tip = tip;

      const top = document.createElement("div");
      top.className = "chart__bar";
      top.style.height = r.first ? `${((r.first - r.second) / r.first) * 100}%` : "0%";
      top.dataset.tip = tip;

      stack.appendChild(top);
      stack.appendChild(bottom);
      wrap.appendChild(stack);
      const label = document.createElement("div");
      label.className = "chart__label";
      label.textContent = r.name;
      wrap.appendChild(label);
      el.appendChild(wrap);
    });
  }

  function renderDynamics() {
    const grouped = groupDaily(state.data.daily, state.granularity).map((r) => ({
      name: r.name, first: r.first, second: r.second,
      conversion: r.first ? Math.round((r.second / r.first) * 1000) / 10 : 0,
      avg_days: null, _sortKey: r.sortKey,
    }));
    renderChart(grouped);
    tables.dyn.setData(grouped);
  }

  function renderSummary(totals) {
    const el = $("#summary");
    const items = [
      { label: "Купили 1‑й абонемент", value: fmtInt(totals.first) },
      { label: "Купили 2‑й абонемент", value: fmtInt(totals.second) },
      { label: "Конверсия", value: fmtPct(totals.conversion), color: badgeColor(totals.conversion) },
      { label: "Среднее число дней до 2‑й покупки", value: totals.avg_days == null ? "—" : totals.avg_days },
    ];
    el.innerHTML = "";
    items.forEach((it) => {
      const card = document.createElement("div");
      card.className = "summary__card";
      card.innerHTML = `<div class="value"${it.color ? ` style="color:${it.color}"` : ""}>${it.value}</div><div class="label">${it.label}</div>`;
      el.appendChild(card);
    });
  }

  function showError(msg) {
    const box = $("#errorBox");
    box.style.display = "block";
    box.textContent = msg;
  }
  function hideError() { $("#errorBox").style.display = "none"; }

  let loadingTimerHandle = null;
  function startLoadingTimer() {
    const t0 = Date.now();
    const el = $("#loadingTimer");
    el.textContent = "0 сек";
    loadingTimerHandle = setInterval(() => {
      el.textContent = `${Math.round((Date.now() - t0) / 1000)} сек`;
    }, 1000);
  }
  function stopLoadingTimer() {
    if (loadingTimerHandle) { clearInterval(loadingTimerHandle); loadingTimerHandle = null; }
  }

  async function loadData(refresh) {
    $("#loading").classList.add("is-visible");
    startLoadingTimer();
    hideError();
    try {
      const df = $("#dateFrom").value, dt = $("#dateTo").value;
      const url = `/api/report?date_from=${df}&date_to=${dt}${refresh ? "&refresh=1" : ""}`;
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Ошибка запроса");
      state.data = data;
      if (data.data_from) $("#dataFrom").textContent = `Учитывается история сделок с ${ruDate(data.data_from)}`;
      renderSummary(data.totals);
      tables.cities.setData(data.tables.cities);
      tables.branches.setData(data.tables.branches);
      tables.managers.setData(data.tables.managers);
      tables.trainers.setData(data.tables.trainers);
      renderDynamics();
      $("#applyBtn").classList.remove("btn--dirty");
    } catch (err) {
      showError(`Не удалось загрузить данные: ${err.message}`);
    } finally {
      stopLoadingTimer();
      $("#loading").classList.remove("is-visible");
    }
  }

  function initControls() {
    $("#applyBtn").addEventListener("click", () => loadData(false));
    $("#refreshBtn").addEventListener("click", () => loadData(true));
    $$("#dateFrom, #dateTo").forEach((el) => el.addEventListener("change", () => $("#applyBtn").classList.add("btn--dirty")));

    $$(".tabs__btn").forEach((btn) => btn.addEventListener("click", () => {
      $$(".tabs__btn").forEach((b) => b.classList.remove("is-active"));
      $$(".tabpanel").forEach((p) => p.classList.remove("is-active"));
      btn.classList.add("is-active");
      $(`.tabpanel[data-panel="${btn.dataset.tab}"]`).classList.add("is-active");
    }));

    $$("#dynGranularity .seg__btn").forEach((btn) => btn.addEventListener("click", () => {
      $$("#dynGranularity .seg__btn").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      state.granularity = btn.dataset.g;
      if (state.data) renderDynamics();
    }));
  }

  initTables();
  initControls();
  loadData(false);
})();
