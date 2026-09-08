(() => {
  const setLoginStatus = (text, state = "info") => {
    const loginStatus = document.querySelector("[data-login-status]");
    if (!loginStatus) return;
    loginStatus.innerHTML = "";
    if (!text) return;
    const message = document.createElement("p");
    message.className = `login-state login-state-${state}`;
    message.setAttribute("role", state === "error" ? "alert" : "status");
    message.textContent = text;
    loginStatus.appendChild(message);
  };

  const isHtmxForm = (form) => form.hasAttribute("hx-boost") || form.hasAttribute("hx-get") || form.hasAttribute("hx-post");
  const setBusy = (form, busy) => {
    form.classList.toggle("is-loading", busy);
    for (const button of form.querySelectorAll("button[type='submit']")) {
      if (busy) button.setAttribute("aria-busy", "true"); else button.removeAttribute("aria-busy");
      if (!isHtmxForm(form)) button.disabled = busy;
    }
  };

  // Prevent double submission: native forms disable their submit buttons after the
  // submit event is dispatched (values of the clicked button are already captured).
  // htmx forms rely on hx-disabled-elt / hx-sync and are only marked as loading.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;
    if (form.hasAttribute("hx-get")) return;
    setBusy(form, true);
    if (form.matches("[data-login-form]")) setLoginStatus("Connexion sécurisée en cours…");
  });
  // Restore forms when the page is served from the back/forward cache.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    for (const form of document.querySelectorAll("form.is-loading")) setBusy(form, false);
  });

  document.body.addEventListener("htmx:beforeSwap", (event) => {
    const form = event.detail.elt?.closest?.("[data-login-form]");
    if (!form) return;
    const destination = event.detail.xhr?.responseURL || "";
    if (destination && !destination.includes("/auth/login/")) setLoginStatus("Identifiants validés. Redirection sécurisée…", "success");
  });
  document.body.addEventListener("htmx:afterRequest", (event) => {
    const form = event.detail.elt?.closest?.("form");
    if (!form || event.detail.successful) return;
    setBusy(form, false);
    if (form.matches("[data-login-form]")) setLoginStatus("La connexion n’a pas pu aboutir. Vérifiez votre réseau et réessayez.", "error");
  });
  document.body.addEventListener("htmx:sendError", (event) => {
    const form = event.detail.elt?.closest?.("form");
    if (!form) return;
    setBusy(form, false);
    if (form.matches("[data-login-form]")) setLoginStatus("La connexion n’a pas pu aboutir. Vérifiez votre réseau et réessayez.", "error");
  });

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest?.("[data-password-toggle]");
    if (!toggle) return;
    const inputId = toggle.getAttribute("aria-controls");
    const input = inputId ? document.getElementById(inputId) : null;
    if (!input) return;
    const willReveal = input.type === "password";
    input.type = willReveal ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(willReveal));
    toggle.setAttribute("aria-label", willReveal ? "Masquer le mot de passe" : "Afficher le mot de passe");
    const openEye = toggle.querySelector(".password-eye-open");
    const closedEye = toggle.querySelector(".password-eye-closed");
    if (openEye) openEye.hidden = willReveal;
    if (closedEye) closedEye.hidden = !willReveal;
  });

  // Responsive tables: copy column headings onto cells so the stacked mobile
  // layout can label every value with the real header text.
  const labelTables = (root = document) => {
    for (const table of root.querySelectorAll(".table-wrap table")) {
      const headers = Array.from(table.querySelectorAll("thead th"), (th) => th.textContent.trim());
      if (!headers.length) continue;
      for (const row of table.querySelectorAll("tbody tr")) {
        Array.from(row.children).forEach((cell, index) => {
          if (headers[index]) cell.setAttribute("data-label", headers[index]);
        });
      }
    }
  };
  labelTables();
  document.body.addEventListener("htmx:afterSwap", (event) => labelTables(event.detail.target ?? document));

  const workspace = document.querySelector("[data-workspace]");
  if (!workspace) return;

  const sidebar = workspace.querySelector("[data-sidebar]");
  const openButton = workspace.querySelector("[data-nav-open]");
  const closeButton = workspace.querySelector("[data-nav-close]");
  const backdrop = workspace.querySelector("[data-nav-backdrop]");
  const profileButton = workspace.querySelector("[data-profile-toggle]");
  const profileDropdown = workspace.querySelector("[data-profile-dropdown]");

  const mobileQuery = window.matchMedia("(max-width: 820px)");
  const isMobile = () => mobileQuery.matches;
  const setNavigation = (open, restoreFocus = false) => {
    sidebar?.classList.toggle("is-open", open);
    if (backdrop) backdrop.hidden = !open;
    openButton?.setAttribute("aria-expanded", String(open));
    if (sidebar) {
      const hidden = isMobile() && !open;
      sidebar.inert = hidden;
      if (hidden) sidebar.setAttribute("aria-hidden", "true"); else sidebar.removeAttribute("aria-hidden");
    }
    document.body.classList.toggle("nav-open", open && isMobile());
    if (open) sidebar?.querySelector("a")?.focus();
    if (!open && restoreFocus) openButton?.focus();
  };

  const setProfile = (open, restoreFocus = false) => {
    if (profileDropdown) profileDropdown.hidden = !open;
    profileButton?.setAttribute("aria-expanded", String(open));
    if (open) profileDropdown?.querySelector("button, a")?.focus();
    if (!open && restoreFocus) profileButton?.focus();
  };

  openButton?.addEventListener("click", () => setNavigation(true));
  closeButton?.addEventListener("click", () => setNavigation(false, true));
  backdrop?.addEventListener("click", () => setNavigation(false, true));
  profileButton?.addEventListener("click", () => setProfile(profileDropdown?.hidden ?? true));
  workspace.addEventListener("click", (event) => {
    if (profileDropdown && !profileDropdown.hidden && !event.target.closest(".profile-menu")) setProfile(false);
    if (isMobile() && event.target.closest(".workspace-link")) setNavigation(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (sidebar?.classList.contains("is-open")) setNavigation(false, true);
    else if (profileDropdown && !profileDropdown.hidden) setProfile(false, true);
  });
  mobileQuery.addEventListener("change", () => setNavigation(false));
  setNavigation(false);
})();
