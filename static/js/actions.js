function updateOpenButtons(id, open) {
  document.querySelectorAll(`[data-open-panel="${id}"]`).forEach((button) => {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    const showLabel = button.getAttribute("data-open-label") || "Open";
    const hideLabel = button.getAttribute("data-close-label") || "Close form";
    button.textContent = open ? hideLabel : showLabel;
  });
}

function syncListPanels() {
  const formOpen = [...document.querySelectorAll("[data-form-panel]")].some((panel) => !panel.hidden);
  document.querySelectorAll("[data-list-panel]").forEach((panel) => {
    panel.hidden = formOpen;
  });
}

function setRegisterPanel(id, open) {
  const panel = document.getElementById(id);
  if (!panel) return;
  if (open) {
    document.querySelectorAll("[data-form-panel]").forEach((other) => {
      if (other.id === id) return;
      other.hidden = true;
      updateOpenButtons(other.id, false);
    });
  }
  panel.hidden = !open;
  updateOpenButtons(id, open);
  syncListPanels();
  if (!open) {
    const url = new URL(window.location.href);
    if (url.searchParams.get("show") === id) {
      url.searchParams.delete("show");
      history.replaceState({}, "", url);
    }
  }
  if (open) {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    const firstField = panel.querySelector("input:not([type=hidden]), select");
    if (firstField) firstField.focus();
  }
}

document.querySelectorAll("[data-open-panel]").forEach((button) => {
  button.addEventListener("click", () => {
    const id = button.getAttribute("data-open-panel");
    const panel = document.getElementById(id);
    setRegisterPanel(id, !panel || panel.hidden);
  });
});

document.querySelectorAll("[data-close-panel]").forEach((button) => {
  button.addEventListener("click", () => setRegisterPanel(button.getAttribute("data-close-panel"), false));
});

document.querySelectorAll("[data-back]").forEach((link) => {
  link.addEventListener("click", (event) => {
    if (window.history.length > 1) {
      event.preventDefault();
      window.history.back();
    }
  });
});

const showPanel = new URLSearchParams(window.location.search).get("show");
if (showPanel && document.getElementById(showPanel)) {
  setRegisterPanel(showPanel, true);
} else {
  syncListPanels();
}

const TABLE_PAGE_SIZE = 10;

function tableDataRows(table) {
  const body = table.tBodies[0];
  if (!body) return [];
  return [...body.querySelectorAll(":scope > tr")].filter((row) => {
    const first = row.cells[0];
    return !(first && first.colSpan > 1 && row.cells.length === 1);
  });
}

function tableBlock(table) {
  let wrap = table.parentElement;
  if (!wrap.classList.contains("table-wrap")) {
    wrap = document.createElement("div");
    wrap.className = "table-wrap";
    table.parentNode.insertBefore(wrap, table);
    wrap.appendChild(table);
  }
  let block = wrap.parentElement;
  if (!block.classList.contains("table-block")) {
    block = document.createElement("div");
    block.className = "table-block";
    wrap.parentNode.insertBefore(block, wrap);
    block.appendChild(wrap);
  }
  return { wrap, block };
}

function paginateTable(table) {
  const rows = tableDataRows(table);
  if (!rows.length) return;

  const { wrap, block } = tableBlock(table);
  if (block.querySelector(":scope > .table-pager")) return;

  let page = 1;
  const pager = document.createElement("nav");
  pager.className = "table-pager";
  pager.setAttribute("aria-label", "Table pages");
  block.appendChild(pager);

  function pageNumbers(current, total) {
    if (total <= 7) {
      return Array.from({ length: total }, (_, index) => index + 1);
    }
    const marks = new Set([1, total, current - 1, current, current + 1]);
    return [...marks].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
  }

  function showPage(scrollToTable = false) {
    const totalPages = Math.max(1, Math.ceil(rows.length / TABLE_PAGE_SIZE));
    page = Math.min(Math.max(page, 1), totalPages);
    const start = (page - 1) * TABLE_PAGE_SIZE;
    const end = Math.min(start + TABLE_PAGE_SIZE, rows.length);
    let visible = 0;
    rows.forEach((row, index) => {
      const hide = index < start || index >= end;
      row.classList.toggle("pager-hide", hide);
      if (!hide) {
        row.classList.toggle("is-alt", visible % 2 === 1);
        visible += 1;
      }
    });
    pager.hidden = totalPages <= 1;
    pager.innerHTML = "";
    if (totalPages <= 1) return;

    const prev = document.createElement("button");
    prev.type = "button";
    prev.className = "btn ghost small";
    prev.textContent = "Previous";
    prev.disabled = page === 1;
    prev.addEventListener("click", () => {
      page -= 1;
      showPage(true);
    });
    const numbers = document.createElement("div");
    numbers.className = "table-pager-pages";
    let last = 0;
    pageNumbers(page, totalPages).forEach((n) => {
      if (last && n > last + 1) {
        const dots = document.createElement("span");
        dots.className = "table-pager-ellipsis";
        dots.textContent = "...";
        numbers.appendChild(dots);
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn ghost small" + (n === page ? " is-current" : "");
      btn.textContent = String(n);
      btn.setAttribute("aria-label", `Page ${n} of ${totalPages}`);
      btn.setAttribute("aria-current", n === page ? "page" : "false");
      btn.addEventListener("click", () => {
        page = n;
        showPage(true);
      });
      numbers.appendChild(btn);
      last = n;
    });
    const status = document.createElement("span");
    status.className = "table-pager-status";
    status.textContent = `Page ${page} of ${totalPages} · ${start + 1}-${end} of ${rows.length} rows`;
    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn ghost small";
    next.textContent = "Next";
    next.disabled = page === totalPages;
    next.addEventListener("click", () => {
      page += 1;
      showPage(true);
    });
    const controls = document.createElement("div");
    controls.className = "table-pager-controls";
    controls.append(prev, numbers, next);
    pager.append(status, controls);
    if (scrollToTable) {
      wrap.scrollIntoView({ block: "nearest" });
    }
  }

  showPage();
}

document.querySelectorAll("table").forEach((table) => {
  tableBlock(table);
  paginateTable(table);
});

function bulkBoxes(formId) {
  return [...document.querySelectorAll(`input[name="student_id"][form="${formId}"]`)];
}

function syncBulkState(formId) {
  const boxes = bulkBoxes(formId);
  const checked = boxes.filter((box) => box.checked).length;
  const master = document.querySelector(`[data-select-all="${formId}"]`);
  if (master) {
    master.checked = boxes.length > 0 && checked === boxes.length;
    master.indeterminate = checked > 0 && checked < boxes.length;
  }
  document.querySelectorAll(`[data-bulk-submit="${formId}"]`).forEach((button) => {
    button.disabled = checked === 0;
    const base = button.getAttribute("data-bulk-label") || "Delete selected";
    button.textContent = checked ? `${base} (${checked})` : base;
  });
}

document.querySelectorAll("[data-bulk-submit]").forEach((button) => {
  if (!button.getAttribute("data-bulk-label")) {
    button.setAttribute("data-bulk-label", button.textContent.trim() || "Delete selected");
  }
  syncBulkState(button.getAttribute("data-bulk-submit"));
});

document.querySelectorAll("[data-select-all]").forEach((master) => {
  master.addEventListener("change", () => {
    bulkBoxes(master.getAttribute("data-select-all")).forEach((box) => {
      box.checked = master.checked;
    });
    syncBulkState(master.getAttribute("data-select-all"));
  });
});

document.addEventListener("change", (event) => {
  const box = event.target.closest('input[name="student_id"][form]');
  if (!box) return;
  syncBulkState(box.getAttribute("form"));
});

document.querySelectorAll("[data-bulk-form]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const count = bulkBoxes(form.id).filter((box) => box.checked).length;
    if (!count) {
      event.preventDefault();
      window.alert("Select at least one student to delete.");
      return;
    }
    const noun = count === 1 ? "student" : "students";
    if (!window.confirm(`Delete ${count} selected ${noun} and all of their attendance, classwork and homework? This cannot be undone.`)) {
      event.preventDefault();
    }
  });
});

document.querySelectorAll("[data-select-group]").forEach((master) => {
  const form = master.closest("form");
  const name = master.getAttribute("data-select-group");
  if (!form || !name) return;
  const boxes = () => [...form.querySelectorAll(`input[type="checkbox"][name="${name}"]`)];
  const sync = () => {
    const all = boxes();
    const checked = all.filter((box) => box.checked).length;
    master.checked = all.length > 0 && checked === all.length;
    master.indeterminate = checked > 0 && checked < all.length;
  };
  master.addEventListener("change", () => {
    boxes().forEach((box) => {
      box.checked = master.checked;
    });
  });
  form.addEventListener("change", (event) => {
    if (event.target.matches(`input[name="${name}"]`)) sync();
  });
});

document.querySelectorAll("form[data-require-group]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const name = form.getAttribute("data-require-group");
    const checked = form.querySelectorAll(`input[name="${name}"]:checked`).length;
    if (!checked) {
      event.preventDefault();
      window.alert("Select at least one class.");
    }
  });
});

const sidebar = document.getElementById("sidebar");
const navToggle = document.querySelector(".nav-toggle");
const appShell = document.querySelector(".app-shell");
const OPEN_KEY = "sidebarOpen";
const desktopQuery = window.matchMedia("(min-width: 901px)");

function isDesktop() {
  return desktopQuery.matches;
}

function setSidebarOpen(open, persist = true) {
  if (!sidebar || !navToggle) return;
  sidebar.classList.toggle("open", open);
  if (appShell) appShell.classList.toggle("sidebar-open", open && isDesktop());
  navToggle.setAttribute("aria-expanded", open ? "true" : "false");
  navToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  navToggle.title = open ? "Close the side menu" : "Open the side menu";
  if (persist && isDesktop()) {
    try {
      localStorage.setItem(OPEN_KEY, open ? "1" : "0");
    } catch {
      /* ignore private-mode storage errors */
    }
  }
}

function restoreSidebar() {
  if (!isDesktop()) {
    setSidebarOpen(false, false);
    return;
  }
  let open = false;
  try {
    open = localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    open = false;
  }
  setSidebarOpen(open, false);
}

if (sidebar && navToggle) {
  restoreSidebar();

  navToggle.addEventListener("click", (event) => {
    event.stopPropagation();
    setSidebarOpen(!sidebar.classList.contains("open"));
  });

  sidebar.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      if (!isDesktop()) setSidebarOpen(false, false);
    });
  });

  desktopQuery.addEventListener("change", restoreSidebar);
}

function placeActionPanel(menu) {
  const panel = menu.querySelector(".action-menu-panel");
  const toggle = menu.querySelector(".action-menu-toggle");
  if (!panel || !toggle) return;
  const rect = toggle.getBoundingClientRect();
  panel.style.position = "fixed";
  panel.style.right = "auto";
  const width = panel.offsetWidth || 210;
  let left = rect.right - width;
  if (left < 8) left = 8;
  if (left + width > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - width - 8);
  }
  let top = rect.bottom + 4;
  const height = panel.offsetHeight || 0;
  if (top + height > window.innerHeight - 8 && rect.top - height - 4 > 8) {
    top = rect.top - height - 4;
  }
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
}

function clearActionPanel(menu) {
  const panel = menu.querySelector(".action-menu-panel");
  if (!panel) return;
  panel.style.position = "";
  panel.style.left = "";
  panel.style.top = "";
  panel.style.right = "";
}

function closeActionMenus(except) {
  document.querySelectorAll(".action-menu.open").forEach((openMenu) => {
    if (openMenu === except) return;
    openMenu.classList.remove("open");
    clearActionPanel(openMenu);
    const button = openMenu.querySelector(".action-menu-toggle");
    if (button) button.setAttribute("aria-expanded", "false");
  });
}

document.addEventListener("click", (event) => {
  const toggle = event.target.closest(".action-menu-toggle");
  const menu = event.target.closest(".action-menu");
  const deleteBtn = event.target.closest("[data-delete-form]");

  if (sidebar && navToggle && !isDesktop() && !event.target.closest("#sidebar")) {
    setSidebarOpen(false);
  }

  closeActionMenus(menu);

  if (toggle && menu) {
    const isOpen = menu.classList.toggle("open");
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    if (isOpen) placeActionPanel(menu);
    else clearActionPanel(menu);
    return;
  }

  if (deleteBtn) {
    event.preventDefault();
    const form = document.querySelector(deleteBtn.getAttribute("data-delete-form"));
    const message = deleteBtn.getAttribute("data-confirm") || "Are you sure?";
    if (form && window.confirm(message)) form.submit();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  setSidebarOpen(false);
  closeActionMenus();
});

window.addEventListener("resize", () => {
  document.querySelectorAll(".action-menu.open").forEach(placeActionPanel);
});

window.addEventListener("scroll", () => {
  document.querySelectorAll(".action-menu.open").forEach(placeActionPanel);
}, true);
